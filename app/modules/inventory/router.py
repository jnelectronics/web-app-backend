import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.inventory.schemas import InventoryAdjustment, InventoryRecordRead
from app.modules.inventory.service import InventoryService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/inventory", tags=["Inventory"])

_manage_roles = require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)


@router.get("/variants/{variant_uuid}")
def list_inventory_for_variant(
    variant_uuid: uuid.UUID,
    principal=Depends(_manage_roles),
    db: Session = Depends(get_db_session),
):
    records = InventoryService(db).list_for_variant(variant_uuid)
    return success_envelope(
        [InventoryRecordRead.model_validate(r) for r in records], "Inventory retrieved successfully."
    )


@router.patch("/variants/{variant_uuid}/branches/{branch_uuid}")
def adjust_inventory(
    variant_uuid: uuid.UUID,
    branch_uuid: uuid.UUID,
    payload: InventoryAdjustment,
    principal=Depends(_manage_roles),
    db: Session = Depends(get_db_session),
):
    record = InventoryService(db).adjust(variant_uuid, branch_uuid, payload, uuid.UUID(principal.id))
    return success_envelope(InventoryRecordRead.model_validate(record), "Inventory adjusted successfully.")


# TODO: full endpoint set (movement history, per-branch listing) per API Specification §5.6.
