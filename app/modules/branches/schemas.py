import uuid

from pydantic import BaseModel, ConfigDict


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str
    phone_number: str | None
    email: str | None
    is_active: bool


class BranchCreate(BaseModel):
    name: str
    address: str
    phone_number: str | None = None
    email: str | None = None


class BranchUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    phone_number: str | None = None
    email: str | None = None


class BranchStatusUpdate(BaseModel):
    is_active: bool
