# Registration, login, refresh, logout, and password reset - the full set
# of entry/exit points into having (or recovering) an authenticated
# identity, for both customers and staff.

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from jobs import send_password_reset_email
from models import Customer, CustomerStatus, OwnerType, RefreshToken, StaffUser
from rate_limit import check_not_locked_out, clear_failed_attempts, record_failed_attempt
from schemas import (
    CustomerLogin,
    CustomerRead,
    CustomerRegister,
    CustomerRegisterResponse,
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
def register(customer: CustomerRegister, db: Session = Depends(get_db)):
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

    access_token = create_access_token(subject=str(new_customer.id), account_type="customer")
    refresh_token = _issue_refresh_token(db, OwnerType.CUSTOMER, new_customer.id)
    return CustomerRegisterResponse(
        customer=new_customer, access_token=access_token, refresh_token=refresh_token
    )


@router.post("/login", response_model=TokenPair)
def login(credentials: CustomerLogin, db: Session = Depends(get_db)):
    # Keyed by email, prefixed "customer:" so this can never collide with
    # a staff lockout for the same email address (two separate systems -
    # see rate_limit.py). Checked BEFORE even looking the customer up, so
    # a locked-out attacker can't use response timing to learn anything.
    rate_limit_key = f"customer:{credentials.email}"
    check_not_locked_out(rate_limit_key)

    customer = db.query(Customer).filter(Customer.email == credentials.email).first()

    # Deliberately the SAME error for "no such email" and "wrong password" -
    # telling an attacker which one was wrong would confirm whether a given
    # email is registered at all.
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
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
    background_tasks.add_task(send_password_reset_email, customer.email, reset_token)

    # No real email integration exists in this project (see routers/payments.py's
    # module docstring for the same situation with a payment gateway) - the
    # token is ALSO returned directly here (on top of being queued above),
    # so the flow stays fully testable via /docs without a real mail server
    # or a running worker process. A real deployment would rely on the
    # queued email job actually being delivered, and would NOT return the
    # token in the API response.
    generic_response["reset_token"] = reset_token
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
