import uuid

from pydantic import BaseModel, ConfigDict

from app.db.enums import MovementType


class InventoryRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_available: int
    quantity_reserved: int


class InventoryAdjustment(BaseModel):
    quantity_changed: int
    movement_type: MovementType = MovementType.ADJUSTMENT
    reason: str | None = None
