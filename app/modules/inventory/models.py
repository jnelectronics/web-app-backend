"""inventory_records, inventory_movements (DB Design Doc §5.10-5.11)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.enums import MovementType


class InventoryRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory_records"
    __table_args__ = (
        UniqueConstraint("variant_id", "branch_id"),
        CheckConstraint("quantity_available >= 0"),
        CheckConstraint("quantity_reserved >= 0"),
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="inventory_record")


class InventoryMovement(UUIDPrimaryKeyMixin, Base):
    """Immutable, append-only — rows are never updated or deleted (SAD §7.11)."""

    __tablename__ = "inventory_movements"

    inventory_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_records.id"), nullable=False
    )
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(MovementType, name="movement_type"), nullable=False
    )
    quantity_changed: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    inventory_record: Mapped["InventoryRecord"] = relationship(back_populates="movements")
