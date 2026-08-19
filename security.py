# This file's job: password hashing and JWT tokens - the two building
# blocks every auth flow needs. Lives here (not in routers/auth.py) because
# other domains will eventually need to verify a token too (e.g. "is this
# request from a logged-in customer?"), not just the auth routes themselves.

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import Customer, StaffRole, StaffUser

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")

# The signing algorithm for our JWTs - HS256 means the same SECRET_KEY both
# signs a token when created and verifies it hasn't been tampered with when
# checked later.
ALGORITHM = "HS256"

# How long a login stays valid before the client has to log in again.
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# How long a refresh token stays valid before the client has to do a full
# login again, not just hit /auth/refresh - much longer than the access
# token, since the whole point of a refresh token is not re-prompting for
# a password every hour.
REFRESH_TOKEN_EXPIRE_DAYS = 30

# How long a password reset link/token stays valid - short, since it's
# meant to be used right after being requested, not saved for later.
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 15

# CryptContext is passlib's wrapper around a hashing scheme - "argon2" here,
# matching the docs. Everywhere else in the app should call hash_password /
# verify_password rather than touching this directly.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(subject: str, account_type: str = "customer") -> str:
    # `subject` is whatever uniquely identifies the logged-in account -
    # we'll pass their id (as a string). "sub" and "exp" are standard JWT
    # claim names: sub = who this token is about, exp = when it expires.
    # `type` is our OWN addition (not a JWT standard) - it stamps whether
    # this token was issued to a customer or a staff account, so the two
    # can never be confused even though both are just "a UUID + expiry"
    # underneath.
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": subject, "exp": expire, "type": account_type}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    # Returns the subject (account id) if the token is valid and not
    # expired, or None if it's been tampered with, expired, or malformed -
    # callers just need to check for None, not catch exceptions themselves.
    # Note: doesn't check `type` - callers that care (get_current_staff)
    # use decode_token_claims instead to see the full payload.
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def decode_token_claims(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def generate_refresh_token() -> str:
    # token_urlsafe generates a long, cryptographically random string - the
    # raw token given to the client. It's never stored anywhere; only its
    # hash (see hash_refresh_token) is, same reasoning as a password.
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    # A fast hash is the right choice here (unlike hash_password's slow
    # Argon2) - see the comment on RefreshToken.token_hash in models.py for
    # why: this is already a high-entropy random secret, not a guessable
    # password, and it needs to be checked cheaply on every refresh call.
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_password_reset_token(subject: str) -> str:
    # A short-lived, self-contained JWT stands in for a real password reset
    # flow - there's no password_reset_tokens table in the DB design (only
    # refresh_tokens), so instead of inventing one, this reuses the same
    # signed-token mechanism as an access token, just with its own "type"
    # claim and a much shorter expiry. Being self-contained also means
    # there's nothing to look up or clean up in the database for it.
    expire = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": subject, "exp": expire, "type": "password_reset"}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# HTTPBearer is what makes Swagger UI show an "Authorize" lock icon where
# you paste a raw token (unlike OAuth2PasswordBearer, which expects a
# username/password login form - we don't need that since our /login
# already takes plain JSON).
#
# auto_error=False is load-bearing, not just a style choice: HTTPBearer's
# own DEFAULT (auto_error=True) raises its own HTTPException(403, "Not
# authenticated") the instant no Authorization header is present at all -
# BEFORE any of the functions below ever run, so their own 401 handling
# never gets a chance to apply. That meant a request with NO token at all
# got 403, while a request with a present-but-garbage/expired token got
# 401 from this file's own checks - two different status codes for what a
# client should treat as the exact same case ("I'm not logged in"),
# forcing frontend code to treat some 403s like 401s as a workaround.
# auto_error=False hands that decision back to each function below, which
# now raises the SAME 401 either way.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Customer:
    # A FastAPI dependency - any route that adds
    # `Depends(get_current_customer)` gets this run automatically before
    # the route body, and either gets back a real Customer or the request
    # is rejected with 401 before the route even executes.
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise invalid_token

    claims = decode_token_claims(credentials.credentials)
    # Reject a staff token here too - "type" is what keeps the two account
    # kinds from being interchangeable just because they're both UUIDs.
    if claims is None or claims.get("type") != "customer":
        raise invalid_token

    customer = db.get(Customer, uuid.UUID(claims["sub"]))
    if customer is None:
        raise invalid_token

    return customer


def get_current_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> StaffUser:
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise invalid_token

    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") != "staff":
        raise invalid_token

    staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
    if staff is None or not staff.is_active:
        raise invalid_token

    return staff


def require_staff_role(*allowed_roles):
    # A dependency FACTORY, not a dependency itself - calling it with
    # specific roles returns a dependency function tailored to that route.
    # e.g. Depends(require_staff_role(StaffRole.OWNER)) only lets Owners
    # through; anyone else gets a 403.
    #
    # System Administrator is a deliberate exception to that per-route list:
    # by product decision, admin is a true superset role that can reach
    # EVERY staff-gated endpoint, not just the ones that name it explicitly.
    # This is a departure from the API spec's literal per-endpoint role
    # table (the spec scopes System Administrator to "seeded account,
    # initial configuration only") - kept here as a single, centralized
    # override so every route that uses require_staff_role() picks it up
    # automatically, instead of having to list System Administrator by
    # hand on every route.
    def check_role(staff: StaffUser = Depends(get_current_staff)) -> StaffUser:
        if staff.role == StaffRole.SYSTEM_ADMINISTRATOR:
            return staff
        if staff.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to perform this action",
            )
        return staff

    return check_role


def get_current_customer_or_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    # Like get_current_customer, but ALSO accepts a System Administrator
    # staff token - admin is the one role with universal access everywhere
    # (see require_staff_role's docstring above for why). Every other
    # staff role is still rejected here, exactly like get_current_customer
    # today - this isn't the broader "any staff" pattern some other routes
    # use (e.g. orders.py's _resolve_order_actor), it's specifically for
    # routes where only the resource's owner OR the super admin should get
    # in, nobody else.
    #
    # Returns a tuple, (actor_type, actor), instead of just one account
    # type, since the caller genuinely got one of two different kinds of
    # row back: ("customer", Customer) or ("admin", StaffUser). The route
    # itself decides what "admin" means for its own ownership check -
    # usually "skip the owner check entirely".
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise invalid_token

    claims = decode_token_claims(credentials.credentials)
    if claims is None:
        raise invalid_token

    if claims.get("type") == "customer":
        customer = db.get(Customer, uuid.UUID(claims["sub"]))
        if customer is None:
            raise invalid_token
        return "customer", customer

    if claims.get("type") == "staff":
        staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
        if staff is None or not staff.is_active or staff.role != StaffRole.SYSTEM_ADMINISTRATOR:
            raise invalid_token
        return "admin", staff

    raise invalid_token
