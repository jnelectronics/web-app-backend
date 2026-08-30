# Kampala door-to-door delivery zones - staff-configurable areas + fees,
# replacing the frontend's flat placeholder delivery estimate. Added
# 2026-08-30 at the client's request during UAT (see CLAUDE.md's dated
# bullet for the full story - starts with Kampala/Mukono/Wakiso, more
# upcountry towns later are just more rows here, nothing town-specific in
# the schema).
#
# Two routers, same "public read vs staff-only write" split as
# routers/store_settings.py: the storefront checkout step needs to list
# active zones with no token at all, while creating/editing/toggling one
# is an admin action. That admin action is deliberately narrower than most
# of this project's other 2026-08-18-widened modules - Nyson's spec says
# "Owner and System Administrator only", so Sales Attendant is NOT
# included here (System Administrator still passes regardless, via
# security.py's require_staff_role superset handling).

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import DeliveryZone, StaffRole, StaffUser
from schemas import DeliveryZoneCreate, DeliveryZoneRead, DeliveryZoneStatusUpdate, DeliveryZoneUpdate
from security import require_staff_role

public_router = APIRouter(prefix="/delivery-zones", tags=["delivery-zones"], route_class=EnvelopeRoute)
admin_router = APIRouter(prefix="/admin/delivery-zones", tags=["delivery-zones"], route_class=EnvelopeRoute)

MANAGE_DELIVERY_ZONE_ROLES = (StaffRole.OWNER,)


def _assert_name_free(db: Session, name: str, *, exclude_id: uuid.UUID | None = None) -> None:
    # Case-insensitive/trimmed check, ON TOP of the DB's own (case-sensitive)
    # unique constraint - "Kampala Central" and "kampala central" would
    # otherwise both slip through as "different" names, exactly the
    # confusing duplicate Nyson's doc asks this endpoint to prevent.
    query = db.query(DeliveryZone).filter(func.lower(DeliveryZone.name) == name.strip().lower())
    if exclude_id is not None:
        query = query.filter(DeliveryZone.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A delivery zone with this name already exists"
        )


@public_router.get("", response_model=list[DeliveryZoneRead])
def list_public_delivery_zones(db: Session = Depends(get_db)):
    # Active zones only, sorted the way the checkout dropdown wants them -
    # an inactive zone should never even be an option a customer can pick.
    return (
        db.query(DeliveryZone)
        .filter(DeliveryZone.is_active == True)  # noqa: E712
        .order_by(DeliveryZone.sort_order, DeliveryZone.name)
        .all()
    )


@admin_router.get("", response_model=list[DeliveryZoneRead])
def list_admin_delivery_zones(
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ZONE_ROLES)),
    db: Session = Depends(get_db),
):
    # Both active AND inactive - the admin list needs to show everything so
    # a deactivated zone can be reactivated later, not just hidden forever.
    return db.query(DeliveryZone).order_by(DeliveryZone.sort_order, DeliveryZone.name).all()


@admin_router.post("", response_model=DeliveryZoneRead, status_code=status.HTTP_201_CREATED)
def create_delivery_zone(
    zone: DeliveryZoneCreate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ZONE_ROLES)),
    db: Session = Depends(get_db),
):
    _assert_name_free(db, zone.name)
    new_zone = DeliveryZone(name=zone.name.strip(), fee=zone.fee, sort_order=zone.sort_order)
    db.add(new_zone)
    try:
        db.commit()
    except IntegrityError:
        # Belt-and-braces: a race between two near-simultaneous creates
        # with the same name could slip past the pre-check above - the
        # model's own unique=True constraint is the real backstop.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A delivery zone with this name already exists"
        )
    db.refresh(new_zone)
    return new_zone


@admin_router.patch("/{zone_id}", response_model=DeliveryZoneRead)
def update_delivery_zone(
    zone_id: uuid.UUID,
    update: DeliveryZoneUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ZONE_ROLES)),
    db: Session = Depends(get_db),
):
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Delivery zone not found")

    if update.name is not None:
        _assert_name_free(db, update.name, exclude_id=zone.id)
        zone.name = update.name.strip()
    if update.fee is not None:
        zone.fee = update.fee

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A delivery zone with this name already exists"
        )
    db.refresh(zone)
    return zone


@admin_router.patch("/{zone_id}/status", response_model=DeliveryZoneRead)
def set_delivery_zone_status(
    zone_id: uuid.UUID,
    update: DeliveryZoneStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ZONE_ROLES)),
    db: Session = Depends(get_db),
):
    # Deactivating a zone never deletes it - existing orders already
    # snapshot their own delivery_fee (see the Order model's comment), so
    # nothing historical depends on this zone staying visible to new
    # checkouts. Just hides it from the public list above.
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Delivery zone not found")

    zone.is_active = update.is_active
    db.commit()
    db.refresh(zone)
    return zone
