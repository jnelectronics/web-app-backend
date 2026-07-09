from pydantic import BaseModel, EmailStr, field_validator

from app.modules.users.schemas import CustomerRead


class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr | None = None
    phone_number: str | None = None
    password: str

    @field_validator("phone_number")
    @classmethod
    def require_email_or_phone(cls, phone_number, info):
        if not phone_number and not info.data.get("email"):
            raise ValueError("Either email or phone_number is required.")
        return phone_number


class LoginRequest(BaseModel):
    identifier: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str


class RegisterResponse(BaseModel):
    customer: CustomerRead
    access_token: str
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenOnly(BaseModel):
    access_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    identifier: str


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str
