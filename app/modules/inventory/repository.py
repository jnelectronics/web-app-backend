import uuid

from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.inventory.models import InventoryMovement, InventoryRecord


class InventoryRepository(BaseRepository[InventoryRecord]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, InventoryRecord)

    def get_by_variant_and_branch(
        self, variant_id: uuid.UUID, branch_id: uuid.UUID
    ) -> InventoryRecord | None:
        return (
            self.db.query(InventoryRecord)
            .filter(InventoryRecord.variant_id == variant_id, InventoryRecord.branch_id == branch_id)
            .first()
        )

    def record_movement(self, movement: InventoryMovement) -> InventoryMovement:
        self.db.add(movement)
        self.db.commit()
        self.db.refresh(movement)
        return movement
