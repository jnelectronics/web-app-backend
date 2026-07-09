import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class VariantAttributeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attribute_name: str
    attribute_value: str


class ProductVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    variant_label: str | None
    price: Decimal
    is_active: bool
    attributes: list[VariantAttributeRead] = []


class ProductVariantCreate(BaseModel):
    sku: str
    variant_label: str | None = None
    price: Decimal
    attributes: dict[str, str] = {}


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: str
    is_primary: bool
    display_order: int


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str | None
    is_featured: bool
    is_discounted: bool
    is_active: bool
    images: list[ProductImageRead] = []
    variants: list[ProductVariantRead] = []


class ProductCreate(BaseModel):
    category_id: uuid.UUID
    name: str
    description: str | None = None
    is_featured: bool = False


class ProductUpdate(BaseModel):
    category_id: uuid.UUID | None = None
    name: str | None = None
    description: str | None = None
    is_featured: bool | None = None


class ProductStatusUpdate(BaseModel):
    is_active: bool
