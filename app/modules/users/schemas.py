import uuid

from pydantic import BaseModel, ConfigDict, EmailStr

from app.db.enums import CustomerStatus, CustomerType, StaffRole


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_type: CustomerType
    full_name: str | None
    email: EmailStr | None
    phone_number: str | None
    status: CustomerStatus


class CustomerUpdate(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None


class CustomerPasswordChange(BaseModel):
    current_password: str
    new_password: str


class CustomerStatusUpdate(BaseModel):
    status: CustomerStatus


class StaffUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    phone_number: str | None
    role: StaffRole
    is_active: bool
