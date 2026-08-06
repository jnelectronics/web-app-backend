# Registration, login, refresh, logout, and password reset - the full set
# of entry/exit points into having (or recovering) an authenticated
# identity, for both customers and staff.

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from google_auth_client import GoogleTokenError
from google_auth_client import is_configured as google_is_configured
from google_auth_client import verify_id_token as verify_google_id_token
from jobs import send_password_reset_email
from routers.cart import merge_guest_cart_into_customer
from models import Customer, CustomerStatus, OwnerType, RefreshToken, StaffUser
from rate_limit import check_not_locked_out, clear_failed_attempts, record_failed_attempt
from schemas import (
    CustomerLogin,
    CustomerRead,
    CustomerRegister,
    CustomerRegisterResponse,
    GoogleSignIn,
    PasswordForgotRequest,
    PasswordResetRequest,
    RefreshRequest,
    StaffLogin,
    TokenPair,
)
from security import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    bearer_scheme,
    create_access_token,
    create_password_reset_token,
    decode_token_claims,
    generate_refresh_token,
    get_current_customer,
    hash_password,
    hash_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"], route_class=EnvelopeRoute)


class GoogleSignInUnavailableError(Exception):
    # Raised when GOOGLE_CLIENT_ID isn't configured yet (see
    # google_auth_client.is_configured) - same idea as routers/payments.py's
    # PaymentsUnavailableError: a clean 503 from the handler registered in
    # main.py, instead of every sign-in attempt failing deep inside a
    # confusing Google verification error.
    pass


def _issue_refresh_token(db: Session, owner_type: OwnerType, owner_id) -> str:
    # Creates the DB row (storing only the HASH) and returns the raw token
    # to hand back to the client - shared by register/login/staff_login so
    # the "how a refresh token gets created" logic lives in exactly one
    # place.
    raw_token = generate_refresh_token()
    db.add(
        RefreshToken(
            owner_type=owner_type,
            owner_id=owner_id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    db.commit()
    return raw_token


@router.post("/register", response_model=CustomerRegisterResponse)
def register(
    customer: CustomerRegister,
    db: Session = Depends(get_db),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
):
    # Reject a duplicate email with a clean 409 - otherwise Postgres's own
    # UNIQUE constraint would raise a raw IntegrityError instead.
    if db.query(Customer).filter(Customer.email == customer.email).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    new_customer = Customer(
        full_name=customer.full_name,
        email=customer.email,
        phone_number=customer.phone_number,
        # Never store the raw password - only ever the hash.
        password_hash=hash_password(customer.password),
    )
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)

    # Not in the original spec (added 2026-08-06) - a shopper who added
    # items to a cart BEFORE creating an account shouldn't lose them the
    # moment they register. Only does anything if X-Guest-Token was
    # actually sent AND that guest has an active cart with items in it.
    if x_guest_token:
        merge_guest_cart_into_customer(db, new_customer.id, x_guest_token)

    access_token = create_access_token(subject=str(new_customer.id), account_type="customer")
    refresh_token = _issue_refresh_token(db, OwnerType.CUSTOMER, new_customer.id)
    return CustomerRegisterResponse(
        customer=new_customer, access_token=access_token, refresh_token=refresh_token
    )


@router.post("/login", response_model=TokenPair)
def login(
    credentials: CustomerLogin,
    db: Session = Depends(get_db),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
):
    # Keyed by identifier, prefixed "customer:" so this can never collide
    # with a staff lockout for the same email address (two separate
    # systems - see rate_limit.py). Checked BEFORE even looking the
    # customer up, so a locked-out attacker can't use response timing to
    # learn anything.
    rate_limit_key = f"customer:{credentials.identifier}"
    check_not_locked_out(rate_limit_key)

    # identifier can be EITHER an email or a phone number (added
    # 2026-08-06) - both are unique columns, so matching either is
    # unambiguous. or_ means "find a Customer where email matches OR
    # phone_number matches" - exactly one column needs to match, not both.
    customer = (
        db.query(Customer)
        .filter(or_(Customer.email == credentials.identifier, Customer.phone_number == credentials.identifier))
        .first()
    )

    # Deliberately the SAME error for "no such account" and "wrong
    # password" - telling an attacker which one was wrong would confirm
    # whether a given identifier is registered at all.
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email/phone number or password"
    )

    if customer is None or customer.password_hash is None:
        record_failed_attempt(rate_limit_key)
        raise invalid_credentials
    if not verify_password(credentials.password, customer.password_hash):
        record_failed_attempt(rate_limit_key)
        raise invalid_credentials

    # The password WAS correct at this point - clear the lockout counter
    # regardless of what happens next (e.g. the deactivated-account check
    # below), since this wasn't a guessing failure.
    clear_failed_attempts(rate_limit_key)

    # Checked AFTER verifying the password (not before) - otherwise the
    # response would leak whether a given email belongs to a deactivated
    # account before even confirming the password was right.
    if customer.status != CustomerStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")

    if x_guest_token:
        merge_guest_cart_into_customer(db, customer.id, x_guest_token)

    access_token = create_access_token(subject=str(customer.id), account_type="customer")
    refresh_token = _issue_refresh_token(db, OwnerType.CUSTOMER, customer.id)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/google", response_model=TokenPair)
def google_sign_in(
    request: GoogleSignIn,
    db: Session = Depends(get_db),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
):
    # No rate limiting here the way /login has (check_not_locked_out etc.) -
    # that exists to slow down someone GUESSING a password. There's no
    # equivalent guessable secret in this flow; forging a token that passes
    # verify_id_token's signature check is a cryptographic problem, not a
    # brute-forceable one.
    if not google_is_configured():
        raise GoogleSignInUnavailableError("Google sign-in will be available soon. Please check back later.")

    try:
        google_user = verify_google_id_token(request.id_token)
    except GoogleTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google sign-in token")

    # Required for BOTH creating a new account and auto-linking to an
    # existing one - an unverified email means Google itself isn't
    # vouching that this person actually owns that address, so we can't
    # trust it for either case.
    if not google_user["email"] or not google_user["email_verified"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    customer = db.query(Customer).filter(Customer.email == google_user["email"]).first()

    if customer is None:
        # New account, no password - password_hash stays NULL, same as a
        # guest row. This customer can only ever sign in via Google unless
        # a "set a password" flow gets added later (not built yet).
        customer = Customer(
            full_name=google_user["name"],
            email=google_user["email"],
            password_hash=None,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
    elif customer.status != CustomerStatus.ACTIVE:
        # Same check /login does, just reached a different way here -
        # a deactivated account shouldn't be usable via ANY sign-in method.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")

    if x_guest_token:
        merge_guest_cart_into_customer(db, customer.id, x_guest_token)

    access_token = create_access_token(subject=str(customer.id), account_type="customer")
    refresh_token = _issue_refresh_token(db, OwnerType.CUSTOMER, customer.id)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/staff/login", response_model=TokenPair)
def staff_login(credentials: StaffLogin, db: Session = Depends(get_db)):
    rate_limit_key = f"staff:{credentials.email}"
    check_not_locked_out(rate_limit_key)

    staff = db.query(StaffUser).filter(StaffUser.email == credentials.email).first()

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
    )

    if staff is None or not staff.is_active:
        record_failed_attempt(rate_limit_key)
        raise invalid_credentials
    if not verify_password(credentials.password, staff.password_hash):
        record_failed_attempt(rate_limit_key)
        raise invalid_credentials

    clear_failed_attempts(rate_limit_key)

    access_token = create_access_token(subject=str(staff.id), account_type="staff")
    refresh_token = _issue_refresh_token(db, OwnerType.STAFF, staff.id)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
def refresh_access_token(request: RefreshRequest, db: Session = Depends(get_db)):
    invalid_token = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    token_row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_refresh_token(request.refresh_token))
        .first()
    )
    if token_row is None:
        raise invalid_token
    if token_row.revoked_at is not None:
        raise invalid_token
    if token_row.expires_at < datetime.now(timezone.utc):
        raise invalid_token

    account_type = "customer" if token_row.owner_type == OwnerType.CUSTOMER else "staff"
    new_access_token = create_access_token(subject=str(token_row.owner_id), account_type=account_type)

    # Not rotated - the SAME refresh token keeps working until it expires
    # or /auth/logout revokes it, rather than issuing (and having to track)
    # a new refresh token on every single call. Simpler at the cost of not
    # having single-use refresh tokens; a reasonable trade-off here since
    # nothing in the docs asks for rotation specifically.
    return TokenPair(access_token=new_access_token, refresh_token=request.refresh_token)


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    # The docs give /auth/logout no request body - just an access token
    # identifying "who". Since there's no way to name ONE specific refresh
    # token to revoke, this revokes every currently-active refresh token
    # for that account - a "log out everywhere" interpretation.
    invalid_token = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") not in ("customer", "staff"):
        raise invalid_token

    owner_type = OwnerType.CUSTOMER if claims["type"] == "customer" else OwnerType.STAFF
    now = datetime.now(timezone.utc)
    active_tokens = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.owner_type == owner_type,
            RefreshToken.owner_id == uuid.UUID(claims["sub"]),
            RefreshToken.revoked_at.is_(None),
        )
        .all()
    )
    for token_row in active_tokens:
        token_row.revoked_at = now
    db.commit()

    return {"message": "Logged out successfully"}


@router.post("/password/forgot")
def forgot_password(
    request: PasswordForgotRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(Customer.email == request.email).first()

    # Same response whether the email exists or not - otherwise this
    # endpoint becomes a way to check which emails are registered.
    generic_response = {"message": "If that email is registered, a reset link has been sent."}
    if customer is None:
        return generic_response

    reset_token = create_password_reset_token(subject=str(customer.id))

    # FastAPI's BackgroundTasks - runs jobs.py's send_password_reset_email
    # AFTER the response has already been sent, in this SAME process, not
    # on a separate worker. Chosen over the RQ+Redis+worker.py setup
    # (still in the codebase, just not wired in here - see CLAUDE.md) for
    # the pilot launch: zero extra cost, since Render's Background Worker
    # service type has no free tier. Trade-off: if this web process
    # restarts mid-task, the task is lost - an acceptable risk for a job
    # this low-stakes, revisit if that ever stops being true.
    background_tasks.add_task(send_password_reset_email, customer.email, reset_token, customer.full_name)

    # jobs.py's send_password_reset_email now sends a REAL email (Resend
    # via email_client.py), so the token is NO LONGER echoed back in
    # this response the way it was while email was still simulated -
    # returning it here would let anyone who can see this API response
    # reset that account's password without ever touching the real email,
    # which defeats the point of a password reset flow. The only way to
    # get the token now is to actually receive the email.
    return generic_response


@router.post("/password/reset")
def reset_password(request: PasswordResetRequest, db: Session = Depends(get_db)):
    invalid_token = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired reset token")

    claims = decode_token_claims(request.token)
    if claims is None or claims.get("type") != "password_reset":
        raise invalid_token

    customer = db.get(Customer, uuid.UUID(claims["sub"]))
    if customer is None:
        raise invalid_token

    customer.password_hash = hash_password(request.new_password)
    db.commit()
    return {"message": "Password reset successfully"}


@router.get("/me", response_model=CustomerRead)
def read_current_customer(current_customer: Customer = Depends(get_current_customer)):
    # No lookup logic here at all - by the time this function body runs,
    # get_current_customer has already turned the Authorization header into
    # a real Customer or rejected the request with 401.
    return current_customer
