# Staff account management. Per the docs, Sales Attendants cannot call
# ANY endpoint here (BR-USER-003) - only Inventory Managers and the System
# Administrator can. There's also no way to create a System Administrator
# through this router at all - that account only ever comes from
# seed_admin.py, run once outside the API.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from models import StaffRole, StaffUser
from schemas import StaffCreate, StaffPasswordChange, StaffRead, StaffUpdate
from security import get_current_staff, hash_password, require_staff_role, verify_password

router = APIRouter(prefix="/staff", tags=["staff"], route_class=EnvelopeRoute)

# Both roles allowed to manage staff accounts, per the docs.
MANAGE_STAFF_ROLES = (StaffRole.INVENTORY_MANAGER, StaffRole.SYSTEM_ADMINISTRATOR)


@router.patch("/me/password")
def change_my_password(
    change: StaffPasswordChange,
    current_staff: StaffUser = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    # Not gated to MANAGE_STAFF_ROLES like the rest of this router - every
    # staff member (including a Sales Attendant, otherwise locked out of
    # this whole module per BR-USER-003) needs to be able to change their
    # OWN password. Mirrors routers/customers.py's change_my_password.
    # Matters especially for seed_admin.py's account, which starts on a
    # well-known default password that must be changeable before a pilot.
    if not verify_password(change.current_password, current_staff.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    current_staff.password_hash = hash_password(change.new_password)
    db.commit()
    return {"message": "Password changed successfully"}


@router.get("", response_model=list[StaffRead])
def list_staff(
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    return db.query(StaffUser).offset(skip).limit(limit).all()


@router.post("", response_model=StaffRead)
def create_staff(
    staff: StaffCreate,
    current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    # No one can create a System Administrator through the API - that
    # account is seeded once, outside the API, by seed_admin.py.
    if staff.role == StaffRole.SYSTEM_ADMINISTRATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create a System Administrator account through this endpoint",
        )

    if db.query(StaffUser).filter(StaffUser.email == staff.email).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")

    new_staff = StaffUser(
        full_name=staff.full_name,
        email=staff.email,
        phone_number=staff.phone_number,
        password_hash=hash_password(staff.password),
        role=staff.role,
    )
    db.add(new_staff)
    db.flush()
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="staff.create",
        resource_type="staff_user",
        resource_id=new_staff.id,
        new_value={"email": new_staff.email, "role": new_staff.role.value},
    )
    db.commit()
    db.refresh(new_staff)
    return new_staff


@router.patch("/{staff_id}", response_model=StaffRead)
def update_staff(
    staff_id: uuid.UUID,
    update: StaffUpdate,
    current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    existing = db.get(StaffUser, staff_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Staff account not found")

    if update.role == StaffRole.SYSTEM_ADMINISTRATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot grant System Administrator through this endpoint",
        )

    previous_value = {"email": existing.email, "role": existing.role.value}
    existing.full_name = update.full_name
    existing.email = update.email
    existing.phone_number = update.phone_number
    existing.role = update.role
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="staff.update",
        resource_type="staff_user",
        resource_id=existing.id,
        previous_value=previous_value,
        new_value={"email": existing.email, "role": existing.role.value},
    )
    db.commit()
    db.refresh(existing)
    return existing


@router.patch("/{staff_id}/status", response_model=StaffRead)
def toggle_staff_status(
    staff_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    # Toggles rather than takes a body - matches the docs' description
    # ("Deactivate/reactivate") as a single flip, not a value the caller sets.
    existing = db.get(StaffUser, staff_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Staff account not found")

    existing.is_active = not existing.is_active
    db.commit()
    db.refresh(existing)
    return existing
