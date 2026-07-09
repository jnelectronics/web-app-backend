import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientInventoryError, NotFoundError
from app.modules.inventory.models import InventoryMovement, InventoryRecord
from app.modules.inventory.repository import InventoryRepository
from app.modules.inventory.schemas import InventoryAdjustment


class InventoryService:
    """FR-INV-001-011: inventory records never go negative (BR-INV-002)."""

    def __init__(self, db: Session) -> None:
        self.repository = InventoryRepository(db)

    def list_for_variant(self, variant_id: uuid.UUID) -> list[InventoryRecord]:
        return (
            self.repository.db.query(InventoryRecord)
            .filter(InventoryRecord.variant_id == variant_id)
            .all()
        )

    def adjust(
        self,
        variant_id: uuid.UUID,
        branch_id: uuid.UUID,
        payload: InventoryAdjustment,
        staff_user_id: uuid.UUID,
    ) -> InventoryRecord:
        record = self.repository.get_by_variant_and_branch(variant_id, branch_id)
        if not record:
            raise NotFoundError("No inventory record exists for this variant at this branch.")

        new_quantity = record.quantity_available + payload.quantity_changed
        if new_quantity < 0:
            raise InsufficientInventoryError(
                "Adjustment would drive quantity_available negative.",
                details={"quantity_available": record.quantity_available},
            )

        record.quantity_available = new_quantity
        self.repository.save(record)
        self.repository.record_movement(
            InventoryMovement(
                inventory_record_id=record.id,
                movement_type=payload.movement_type,
                quantity_changed=payload.quantity_changed,
                reason=payload.reason,
                staff_user_id=staff_user_id,
            )
        )
        return record
