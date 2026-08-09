# All the HTTP routes for the "store settings" domain live here - a
# SINGLETON resource (see models.StoreSettings's own comment: exactly one
# row, seeded by the migration that creates the table, never created or
# deleted through the API). Two routers, same "different prefixes for a
# public vs staff view of the same underlying data" reasoning as
# routers/categories.py's group listing - the storefront catalogue
# sidebar needs to read catalogue_filter_mode with no token at all, while
# changing it is a staff-only admin action.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import StaffRole, StaffUser, StoreSettings
from schemas import StoreSettingsRead, StoreSettingsUpdate
from security import require_staff_role

public_router = APIRouter(prefix="/store-settings", tags=["store-settings"], route_class=EnvelopeRoute)
admin_router = APIRouter(prefix="/admin/store-settings", tags=["store-settings"], route_class=EnvelopeRoute)


def _get_settings_row(db: Session) -> StoreSettings:
    # Always exactly one row, seeded by the Alembic migration that created
    # this table - a missing row here would mean that migration never ran,
    # which is a deployment problem, not something a request can recover
    # from on its own.
    settings = db.query(StoreSettings).first()
    if settings is None:
        raise HTTPException(status_code=500, detail="Store settings are not configured")
    return settings


@public_router.get("", response_model=StoreSettingsRead)
def read_public_store_settings(db: Session = Depends(get_db)):
    return _get_settings_row(db)


@admin_router.get("", response_model=StoreSettingsRead)
def read_admin_store_settings(
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    return _get_settings_row(db)


@admin_router.put("", response_model=StoreSettingsRead)
def update_store_settings(
    update: StoreSettingsUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    settings = _get_settings_row(db)
    settings.catalogue_filter_mode = update.catalogue_filter_mode
    db.commit()
    db.refresh(settings)
    return settings
