# All the HTTP routes for the "inventory" domain live here.
# Unlike the other domains, inventory records aren't soft-deleted (there's
# no is_active column) - a zero-stock row is still meaningful, it just
# means "this branch currently has none of this variant."

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from models import Branch, InventoryMovement, InventoryRecord, MovementType, ProductVariant, StaffRole, StaffUser
from pagination import build_pagination_meta
from schemas import InventoryAdjust, InventoryCreate, InventoryMovementRead, InventoryRead, PaginatedResponse
from security import require_staff_role

# Both roles allowed to just VIEW inventory, per the docs - only Inventory
# Manager can create records or adjust quantities (see routes below).
VIEW_INVENTORY_ROLES = (StaffRole.SALES_ATTENDANT, StaffRole.OWNER)


# A custom exception for the one inventory-specific business rule: stock can
# never go negative. Living here (not as a plain HTTPException) means the
# rule is expressed in code as "insufficient inventory", independent of how
# it eventually gets turned into an HTTP response.
class InsufficientInventoryError(Exception):
    def __init__(self, message: str, short_items: list[dict] | None = None):
        # short_items is optional - only routers/orders.py's checkout
        # populates it (it's the only call site with a whole cart's worth
        # of items to report on); the adjust endpoint below just raises
        # with a message, same as before.
        super().__init__(message)
        self.short_items = short_items


router = APIRouter(prefix="/inventory", tags=["inventory"], route_class=EnvelopeRoute)


# Not in the original spec - the only movement endpoint used to be scoped
# to ONE inventory record, meaning a branch-wide "what's moved here
# recently" view (the admin dashboard's stock history) needed one request
# PER inventory record - an N+1 that grows with the catalogue. Registered
# BEFORE "/{inventory_id}" below, same reasoning as routers/orders.py's
# "/staff" - a fixed path ahead of a param path that could otherwise
# swallow it (see CLAUDE.md's route-registration-order gotcha).
@router.get("/movements", response_model=PaginatedResponse[InventoryMovementRead])
def list_movements(
    branch_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_INVENTORY_ROLES)),
    db: Session = Depends(get_db),
):
    # branch_id/variant_id aren't columns on InventoryMovement itself - it
    # only points at inventory_record_id - so filtering by either means
    # joining through InventoryRecord first.
    query = db.query(InventoryMovement).join(
        InventoryRecord, InventoryMovement.inventory_record_id == InventoryRecord.id
    )

    if branch_id is not None:
        query = query.filter(InventoryRecord.branch_id == branch_id)
    if variant_id is not None:
        query = query.filter(InventoryRecord.variant_id == variant_id)
    if movement_type is not None:
        query = query.filter(InventoryMovement.movement_type == movement_type)
    if start_date is not None:
        query = query.filter(InventoryMovement.created_at >= start_date)
    if end_date is not None:
        query = query.filter(InventoryMovement.created_at <= end_date)

    query = query.order_by(InventoryMovement.created_at.desc())
    total = query.count()
    movements = query.offset(skip).limit(limit).all()
    return PaginatedResponse[InventoryMovementRead](
        items=movements, pagination=build_pagination_meta(skip, limit, total)
    )


@router.get("/{inventory_id}", response_model=InventoryRead)
def read_inventory_record(
    inventory_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_INVENTORY_ROLES)),
    db: Session = Depends(get_db),
):
    record = db.get(InventoryRecord, inventory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inventory record not found")
    return record


@router.get("", response_model=PaginatedResponse[InventoryRead])
def list_inventory_records(
    branch_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_INVENTORY_ROLES)),
    db: Session = Depends(get_db),
):
    query = db.query(InventoryRecord)

    # Both filters are optional and combinable - e.g. ?branch_id=X&variant_id=Y
    # narrows to the exact (variant, branch) row.
    if branch_id is not None:
        query = query.filter(InventoryRecord.branch_id == branch_id)
    if variant_id is not None:
        query = query.filter(InventoryRecord.variant_id == variant_id)

    total = query.count()
    records = query.offset(skip).limit(limit).all()
    return PaginatedResponse[InventoryRead](items=records, pagination=build_pagination_meta(skip, limit, total))


@router.post("", response_model=InventoryRead)
def create_inventory_record(
    record: InventoryCreate,
    # Not listed in the docs' endpoint table at all - Owner (and, since the
    # 2026-08-18 RBAC widening, Sales Attendant too) required, same tier as
    # the adjust endpoint below.
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    if db.get(ProductVariant, record.variant_id) is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    if db.get(Branch, record.branch_id) is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    new_record = InventoryRecord(
        variant_id=record.variant_id,
        branch_id=record.branch_id,
        quantity_available=record.quantity_available,
        quantity_reserved=record.quantity_reserved,
    )
    db.add(new_record)
    try:
        db.flush()
    except IntegrityError:
        # Hits the UNIQUE(variant_id, branch_id) constraint - this
        # variant/branch pair already has a stock row, so create a new one
        # doesn't make sense; the caller should adjust the existing one instead.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inventory record already exists for this variant/branch pair",
        )

    # Log the record's starting stock as a movement, same as any other
    # quantity change - skipped for a record created with zero stock, since
    # nothing actually moved yet.
    if record.quantity_available > 0:
        db.add(
            InventoryMovement(
                inventory_record_id=new_record.id,
                movement_type=MovementType.STOCK_IN,
                quantity_changed=record.quantity_available,
                reason="Initial stock",
                staff_user_id=current_staff.id,
            )
        )

    db.commit()
    db.refresh(new_record)
    return new_record


@router.patch("/{inventory_id}/adjust", response_model=InventoryRead)
def adjust_inventory_quantity(
    inventory_id: uuid.UUID,
    adjustment: InventoryAdjust,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    record = db.get(InventoryRecord, inventory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inventory record not found")

    new_quantity = record.quantity_available + adjustment.quantity_change
    if new_quantity < 0:
        raise InsufficientInventoryError(
            f"Cannot apply change of {adjustment.quantity_change}: "
            f"only {record.quantity_available} available"
        )

    previous_quantity = record.quantity_available
    record.quantity_available = new_quantity
    db.add(
        InventoryMovement(
            inventory_record_id=record.id,
            movement_type=adjustment.movement_type,
            quantity_changed=adjustment.quantity_change,
            reason=adjustment.reason,
            staff_user_id=current_staff.id,
        )
    )
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="inventory.adjust",
        resource_type="inventory_record",
        resource_id=record.id,
        previous_value={"quantity_available": previous_quantity},
        new_value={"quantity_available": new_quantity},
    )
    db.commit()
    db.refresh(record)
    return record


@router.get("/{inventory_id}/movements", response_model=PaginatedResponse[InventoryMovementRead])
def read_inventory_movements(
    inventory_id: uuid.UUID,
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_INVENTORY_ROLES)),
    db: Session = Depends(get_db),
):
    if db.get(InventoryRecord, inventory_id) is None:
        raise HTTPException(status_code=404, detail="Inventory record not found")

    query = db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == inventory_id)
    total = query.count()
    movements = query.order_by(InventoryMovement.created_at).offset(skip).limit(limit).all()
    return PaginatedResponse[InventoryMovementRead](
        items=movements, pagination=build_pagination_meta(skip, limit, total)
    )
