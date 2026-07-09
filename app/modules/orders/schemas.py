import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.db.enums import OrderStatus


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variant_id: uuid.UUID
    product_name_snapshot: str
    variant_label_snapshot: str | None
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    delivery_address: str
    subtotal: Decimal
    total: Decimal
    items: list[OrderItemRead] = []


class OrderItemInput(BaseModel):
    variant_id: uuid.UUID
    quantity: int


class OrderCreate(BaseModel):
    guest_full_name: str
    guest_phone_number: str
    guest_email: str | None = None
    delivery_address: str
    items: list[OrderItemInput]


class OrderStatusTransition(BaseModel):
    to_status: OrderStatus
    notes: str | None = None


# FR-ORDER-012/013: valid forward-only transitions, matching the SRS lifecycle table.
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}
