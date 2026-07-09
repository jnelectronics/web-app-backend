import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.db.enums import PaymentStatus


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    provider: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    provider_reference: str | None


class PaymentInitiate(BaseModel):
    order_id: uuid.UUID
    provider: str
    amount: Decimal


class PaymentWebhookPayload(BaseModel):
    provider_reference: str
    status: PaymentStatus
