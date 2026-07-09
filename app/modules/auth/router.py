from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.modules.auth.schemas import (
    AccessTokenOnly,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenPair,
)
from app.modules.auth.service import AuthService
from app.modules.users.schemas import CustomerRead
from app.utils.responses import success_envelope

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db_session)):
    customer, access_token, refresh_token = AuthService(db).register_customer(payload)
    response = RegisterResponse(
        customer=CustomerRead.model_validate(customer),
        access_token=access_token,
        refresh_token=refresh_token,
    )
    return success_envelope(response, "Registration successful.")


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db_session)):
    access_token, refresh_token = AuthService(db).login_customer(payload)
    return success_envelope(TokenPair(access_token=access_token, refresh_token=refresh_token), "Login successful.")


@router.post("/staff/login")
def staff_login(payload: LoginRequest, db: Session = Depends(get_db_session)):
    access_token, refresh_token = AuthService(db).login_staff(payload)
    return success_envelope(TokenPair(access_token=access_token, refresh_token=refresh_token), "Login successful.")


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db_session)):
    access_token = AuthService(db).refresh_access_token(payload.refresh_token)
    return success_envelope(AccessTokenOnly(access_token=access_token), "Token refreshed successfully.")


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db_session)):
    AuthService(db).logout(payload.refresh_token)
    return success_envelope(None, "Logged out successfully.")


@router.post("/password/forgot")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db_session)):
    # TODO: enqueue password-reset email via app/workers/email_worker.py (FR-AUTH-006).
    return success_envelope(None, "If an account exists, a password reset email has been sent.")


@router.post("/password/reset")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db_session)):
    # TODO: validate reset_token and update the matching account's password_hash (FR-AUTH-007).
    return success_envelope(None, "Password has been reset successfully.")
