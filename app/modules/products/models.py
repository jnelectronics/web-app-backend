"""products, product_images, product_variants, variant_attributes (DB Design Doc §5.6-5.9)."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_discounted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    images: Mapped[list["ProductImage"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductImage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    cloudinary_public_id: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    product: Mapped["Product"] = relationship(back_populates="images")


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    variant_label: Mapped[str | None] = mapped_column(String(150))
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped["Product"] = relationship(back_populates="variants")
    attributes: Mapped[list["VariantAttribute"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )


class VariantAttribute(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "variant_attributes"
    __table_args__ = (UniqueConstraint("variant_id", "attribute_name"),)

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=False
    )
    attribute_name: Mapped[str] = mapped_column(String(50), nullable=False)
    attribute_value: Mapped[str] = mapped_column(String(150), nullable=False)

    variant: Mapped["ProductVariant"] = relationship(back_populates="attributes")
