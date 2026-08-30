# Kampala delivery/pickup rework (2026-08-30 UAT) - replaces the flat
# routers/delivery_zones.py (DeliveryZone) shipped earlier the same day.
# Nyson's frontend rebuilt the checkout fulfillment step around THREE
# concepts instead of one flat list:
#   - DeliveryDivision  - a Kampala grouping label (e.g. "Nakawa")
#   - DeliveryArea      - a door-to-door area WITHIN a division, with its
#                         own fee (e.g. "Ntinda" under "Nakawa")
#   - RegionalPickupStation - a flat, un-nested upcountry pickup point,
#                         each with its own fee
# See models.py's comments on each of the three for the full shape.
#
# Same "public read vs staff-only write" split every settings-style router
# in this project already uses (routers/store_settings.py,
# routers/delivery_zones.py before it) - three entities, so three pairs of
# routers (public + admin) in this one file, same reasoning
# routers/categories.py bundles its router + group_router together.
#
# Admin access is Owner + System Administrator only, per Nyson's spec -
# Sales Attendant is NOT included (System Administrator still passes
# regardless, via security.py's require_staff_role superset handling).

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import DeliveryArea, DeliveryDivision, RegionalPickupStation, StaffRole, StaffUser
from schemas import (
    DeliveryAreaCreate,
    DeliveryAreaRead,
    DeliveryAreaStatusUpdate,
    DeliveryAreaUpdate,
    DeliveryAreaWithDivisionRead,
    DeliveryDivisionCreate,
    DeliveryDivisionRead,
    DeliveryDivisionStatusUpdate,
    DeliveryDivisionUpdate,
    RegionalPickupStationCreate,
    RegionalPickupStationRead,
    RegionalPickupStationStatusUpdate,
    RegionalPickupStationUpdate,
)
from security import require_staff_role

division_public_router = APIRouter(
    prefix="/delivery-divisions", tags=["delivery"], route_class=EnvelopeRoute
)
division_admin_router = APIRouter(
    prefix="/admin/delivery-divisions", tags=["delivery"], route_class=EnvelopeRoute
)
area_public_router = APIRouter(prefix="/delivery-areas", tags=["delivery"], route_class=EnvelopeRoute)
area_admin_router = APIRouter(
    prefix="/admin/delivery-areas", tags=["delivery"], route_class=EnvelopeRoute
)
station_public_router = APIRouter(
    prefix="/regional-pickup-stations", tags=["delivery"], route_class=EnvelopeRoute
)
station_admin_router = APIRouter(
    prefix="/admin/regional-pickup-stations", tags=["delivery"], route_class=EnvelopeRoute
)

MANAGE_DELIVERY_ROLES = (StaffRole.OWNER,)


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------


@division_public_router.get("", response_model=list[DeliveryDivisionRead])
def list_public_delivery_divisions(db: Session = Depends(get_db)):
    return (
        db.query(DeliveryDivision)
        .filter(DeliveryDivision.is_active == True)  # noqa: E712
        .order_by(DeliveryDivision.sort_order, DeliveryDivision.name)
        .all()
    )


@division_admin_router.get("", response_model=list[DeliveryDivisionRead])
def list_admin_delivery_divisions(
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    # Active AND inactive - the admin tab needs to show everything so a
    # deactivated division can be reactivated later, not just hidden.
    return db.query(DeliveryDivision).order_by(DeliveryDivision.sort_order, DeliveryDivision.name).all()


@division_admin_router.post("", response_model=DeliveryDivisionRead, status_code=status.HTTP_201_CREATED)
def create_delivery_division(
    division: DeliveryDivisionCreate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    new_division = DeliveryDivision(name=division.name.strip(), sort_order=division.sort_order)
    db.add(new_division)
    db.commit()
    db.refresh(new_division)
    return new_division


@division_admin_router.patch("/{division_id}", response_model=DeliveryDivisionRead)
def update_delivery_division(
    division_id: uuid.UUID,
    update: DeliveryDivisionUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    division = db.get(DeliveryDivision, division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Delivery division not found")

    if update.name is not None:
        division.name = update.name.strip()
    db.commit()
    db.refresh(division)
    return division


@division_admin_router.patch("/{division_id}/status", response_model=DeliveryDivisionRead)
def set_delivery_division_status(
    division_id: uuid.UUID,
    update: DeliveryDivisionStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    # Deactivating a division never deletes it, and never cascades to its
    # areas - existing orders already snapshot delivery_division_name/
    # delivery_fee (see the Order model's comment), so nothing historical
    # depends on this division staying visible to new checkouts.
    division = db.get(DeliveryDivision, division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Delivery division not found")

    division.is_active = update.is_active
    db.commit()
    db.refresh(division)
    return division


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


@area_public_router.get("", response_model=list[DeliveryAreaRead])
def list_public_delivery_areas(
    division_id: uuid.UUID = Query(..., description="Only areas belonging to this division are returned"),
    db: Session = Depends(get_db),
):
    return (
        db.query(DeliveryArea)
        .filter(DeliveryArea.division_id == division_id, DeliveryArea.is_active == True)  # noqa: E712
        .order_by(DeliveryArea.sort_order, DeliveryArea.name)
        .all()
    )


def _area_with_division_name(db: Session, area: DeliveryArea) -> DeliveryAreaWithDivisionRead:
    division = db.get(DeliveryDivision, area.division_id)
    return DeliveryAreaWithDivisionRead(
        id=area.id,
        division_id=area.division_id,
        name=area.name,
        fee=area.fee,
        is_active=area.is_active,
        sort_order=area.sort_order,
        created_at=area.created_at,
        updated_at=area.updated_at,
        # A division is never hard-deleted (only deactivated - see
        # set_delivery_division_status above), so this should never
        # actually be missing - the "?" fallback only guards against that
        # theoretically-impossible case rather than crashing the whole
        # admin list over one bad row.
        division_name=division.name if division is not None else "?",
    )


@area_admin_router.get("", response_model=list[DeliveryAreaWithDivisionRead])
def list_admin_delivery_areas(
    # Optional here (unlike the public read) - the admin settings page
    # shows a "Kampala tab" per division, but may also want a full list
    # across every division at once.
    division_id: uuid.UUID | None = None,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    query = db.query(DeliveryArea)
    if division_id is not None:
        query = query.filter(DeliveryArea.division_id == division_id)
    areas = query.order_by(DeliveryArea.sort_order, DeliveryArea.name).all()
    return [_area_with_division_name(db, a) for a in areas]


@area_admin_router.post("", response_model=DeliveryAreaWithDivisionRead, status_code=status.HTTP_201_CREATED)
def create_delivery_area(
    area: DeliveryAreaCreate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    if db.get(DeliveryDivision, area.division_id) is None:
        raise HTTPException(status_code=404, detail="Delivery division not found")

    new_area = DeliveryArea(
        division_id=area.division_id, name=area.name.strip(), fee=area.fee, sort_order=area.sort_order
    )
    db.add(new_area)
    db.commit()
    db.refresh(new_area)
    return _area_with_division_name(db, new_area)


@area_admin_router.patch("/{area_id}", response_model=DeliveryAreaWithDivisionRead)
def update_delivery_area(
    area_id: uuid.UUID,
    update: DeliveryAreaUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    area = db.get(DeliveryArea, area_id)
    if area is None:
        raise HTTPException(status_code=404, detail="Delivery area not found")

    if update.division_id is not None:
        if db.get(DeliveryDivision, update.division_id) is None:
            raise HTTPException(status_code=404, detail="Delivery division not found")
        area.division_id = update.division_id
    if update.name is not None:
        area.name = update.name.strip()
    if update.fee is not None:
        area.fee = update.fee
    db.commit()
    db.refresh(area)
    return _area_with_division_name(db, area)


@area_admin_router.patch("/{area_id}/status", response_model=DeliveryAreaWithDivisionRead)
def set_delivery_area_status(
    area_id: uuid.UUID,
    update: DeliveryAreaStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    area = db.get(DeliveryArea, area_id)
    if area is None:
        raise HTTPException(status_code=404, detail="Delivery area not found")

    area.is_active = update.is_active
    db.commit()
    db.refresh(area)
    return _area_with_division_name(db, area)


# ---------------------------------------------------------------------------
# Regional pickup stations
# ---------------------------------------------------------------------------


@station_public_router.get("", response_model=list[RegionalPickupStationRead])
def list_public_regional_pickup_stations(db: Session = Depends(get_db)):
    return (
        db.query(RegionalPickupStation)
        .filter(RegionalPickupStation.is_active == True)  # noqa: E712
        .order_by(RegionalPickupStation.sort_order, RegionalPickupStation.major_town)
        .all()
    )


@station_admin_router.get("", response_model=list[RegionalPickupStationRead])
def list_admin_regional_pickup_stations(
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    return (
        db.query(RegionalPickupStation)
        .order_by(RegionalPickupStation.sort_order, RegionalPickupStation.major_town)
        .all()
    )


@station_admin_router.post(
    "", response_model=RegionalPickupStationRead, status_code=status.HTTP_201_CREATED
)
def create_regional_pickup_station(
    station: RegionalPickupStationCreate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    new_station = RegionalPickupStation(
        major_town=station.major_town.strip(),
        address=station.address.strip(),
        fee=station.fee,
        contact=station.contact.strip(),
        sort_order=station.sort_order,
    )
    db.add(new_station)
    db.commit()
    db.refresh(new_station)
    return new_station


@station_admin_router.patch("/{station_id}", response_model=RegionalPickupStationRead)
def update_regional_pickup_station(
    station_id: uuid.UUID,
    update: RegionalPickupStationUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    station = db.get(RegionalPickupStation, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Regional pickup station not found")

    if update.major_town is not None:
        station.major_town = update.major_town.strip()
    if update.address is not None:
        station.address = update.address.strip()
    if update.fee is not None:
        station.fee = update.fee
    if update.contact is not None:
        station.contact = update.contact.strip()
    db.commit()
    db.refresh(station)
    return station


@station_admin_router.patch("/{station_id}/status", response_model=RegionalPickupStationRead)
def set_regional_pickup_station_status(
    station_id: uuid.UUID,
    update: RegionalPickupStationStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_DELIVERY_ROLES)),
    db: Session = Depends(get_db),
):
    station = db.get(RegionalPickupStation, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Regional pickup station not found")

    station.is_active = update.is_active
    db.commit()
    db.refresh(station)
    return station
