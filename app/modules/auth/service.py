import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import create_token, hash_password, verify_password
from app.db.enums import CustomerStatus, CustomerType, OwnerType
from app.modules.auth.repository import RefreshTokenRepository
from app.modules.auth.schemas import LoginRequest, RegisterRequest
from app.modules.users.models import Customer, StaffUser
from app.modules.users.repository import CustomerRepository, StaffUserRepository

CUSTOMER_ROLE = "registered_customer"


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.customers = CustomerRepository(db)
        self.staff = StaffUserRepository(db)
        self.refresh_tokens = RefreshTokenRepository(db)

    def register_customer(self, payload: RegisterRequest) -> tuple[Customer, str, str]:
        if payload.email and self.customers.get_by_identifier(payload.email):
            raise ConflictError("An account with this email already exists.")
        if payload.phone_number and self.customers.get_by_identifier(payload.phone_number):
            raise ConflictError("An account with this phone number already exists.")

        customer = Customer(
            customer_type=CustomerType.REGISTERED,
            full_name=payload.full_name,
            email=payload.email,
            phone_number=payload.phone_number,
            password_hash=hash_password(payload.password),
            status=CustomerStatus.ACTIVE,
        )
        customer = self.customers.create(customer)
        access_token, refresh_token = self._issue_tokens(str(customer.id), CUSTOMER_ROLE, OwnerType.CUSTOMER)
        return customer, access_token, refresh_token

    def login_customer(self, payload: LoginRequest) -> tuple[str, str]:
        customer = self.customers.get_by_identifier(payload.identifier)
        if not customer or not customer.password_hash or not verify_password(
            payload.password, customer.password_hash
        ):
            raise UnauthorizedError("Invalid credentials.")
        return self._issue_tokens(str(customer.id), CUSTOMER_ROLE, OwnerType.CUSTOMER)

    def login_staff(self, payload: LoginRequest) -> tuple[str, str]:
        staff = self.staff.get_by_identifier(payload.identifier)
        if not staff or not verify_password(payload.password, staff.password_hash):
            raise UnauthorizedError("Invalid credentials.")
        if not staff.is_active:
            raise UnauthorizedError("This account has been deactivated.")
        return self._issue_tokens(str(staff.id), staff.role.value, OwnerType.STAFF)

    def refresh_access_token(self, raw_refresh_token: str) -> str:
        token = self.refresh_tokens.get_active(raw_refresh_token)
        if not token:
            raise UnauthorizedError("Invalid or expired refresh token.")
        if token.owner_type == OwnerType.CUSTOMER:
            role = CUSTOMER_ROLE
        else:
            staff = self.staff.get(token.owner_id)
            role = staff.role.value if staff else CUSTOMER_ROLE
        return create_token(str(token.owner_id), role, "access")

    def logout(self, raw_refresh_token: str) -> None:
        token = self.refresh_tokens.get_active(raw_refresh_token)
        if token:
            self.refresh_tokens.revoke(token)

    def _issue_tokens(self, subject: str, role: str, owner_type: OwnerType) -> tuple[str, str]:
        access_token = create_token(subject, role, "access")
        refresh_token = create_token(subject, role, "refresh")
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        self.refresh_tokens.store(
            owner_type=owner_type,
            owner_id=uuid.UUID(subject),
            raw_token=refresh_token,
            expires_at=expires_at,
        )
        return access_token, refresh_token
