import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.db.enums import DiscountType


class BannerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    image_url: str
    display_order: int
    is_active: bool
    starts_at: datetime | None
    ends_at: datetime | None


class BannerCreate(BaseModel):
    title: str
    image_url: str
    display_order: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ProductDiscountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    discount_type: DiscountType
    discount_value: Decimal
    is_active: bool


class ProductDiscountCreate(BaseModel):
    product_id: uuid.UUID
    discount_type: DiscountType
    discount_value: Decimal
    starts_at: datetime | None = None
    ends_at: datetime | None = None
