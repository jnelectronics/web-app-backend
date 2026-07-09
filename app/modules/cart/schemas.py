import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.db.enums import CartStatus


class CartItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    quantity: int
    unit_price_snapshot: Decimal


class CartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CartStatus
    items: list[CartItemRead] = []


class CartItemUpsert(BaseModel):
    variant_id: uuid.UUID
    quantity: int
