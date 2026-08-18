# Staff account management. Per the docs, Sales Attendants cannot call
# ANY endpoint here (BR-USER-003) - only Owners and the System
# Administrator can. There's also no way to create a System Administrator
# through this router at all - that account only ever comes from
# seed_admin.py, run once outside the API.

import secrets
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from jobs import send_staff_welcome_email
from models import StaffRole, StaffUser
from pagination import build_pagination_meta
from schemas import (
    PaginatedResponse,
    StaffCreate,
    StaffPasswordChange,
    StaffPasswordResetResult,
    StaffProfileUpdate,
    StaffRead,
    StaffStatusUpdate,
    StaffUpdate,
)
from security import get_current_staff, hash_password, require_staff_role, verify_password

router = APIRouter(prefix="/staff", tags=["staff"], route_class=EnvelopeRoute)

# Both roles allowed to manage staff accounts, per the docs.
MANAGE_STAFF_ROLES = (StaffRole.OWNER, StaffRole.SYSTEM_ADMINISTRATOR)


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
    # Setting your OWN password for real (as opposed to being handed a
    # temporary one by an admin, or one from account creation) is exactly
    # the event that clears the forced-reset flag - see StaffUser
    # .must_change_password in models.py for the full lifecycle.
    current_staff.must_change_password = False
    db.commit()
    return {"message": "Password changed successfully"}


@router.get("/me", response_model=StaffRead)
def read_my_staff_profile(current_staff: StaffUser = Depends(get_current_staff)):
    # Not gated to MANAGE_STAFF_ROLES, same reasoning as change_my_password
    # above - every staff member needs to be able to see their OWN profile
    # (name, role) regardless of role, not just Owner/System
    # Administrator. Registered BEFORE "/{staff_id}" below so "me" is never
    # matched as a (not-yet-validated) staff_id - see CLAUDE.md's route-
    # registration-order gotcha.
    return current_staff


@router.patch("/me", response_model=StaffRead)
def update_my_staff_profile(
    update: StaffProfileUpdate,
    current_staff: StaffUser = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    # Not gated to MANAGE_STAFF_ROLES, same reasoning as /me and
    # /me/password above - every staff role (including Sales Attendant)
    # can update their OWN name/phone number. StaffProfileUpdate itself
    # doesn't even have role/email/is_active fields, so there's nothing
    # here that could escalate a staff member's own access.
    #
    # full_name follows routers/customers.py's update_my_profile pattern
    # (omitted or null both mean "leave alone" - full_name can't be
    # cleared, it's NOT NULL in the DB). phone_number is different: the
    # frontend needs to be able to explicitly CLEAR it to null, so this
    # checks model_fields_set to tell "field left out of the request"
    # (leave alone) apart from "field sent as null" (clear it) - a plain
    # `if update.phone_number is not None` couldn't distinguish those two.
    if update.full_name is not None:
        current_staff.full_name = update.full_name
    if "phone_number" in update.model_fields_set:
        current_staff.phone_number = update.phone_number
    db.commit()
    db.refresh(current_staff)
    return current_staff


@router.get("", response_model=PaginatedResponse[StaffRead])
def list_staff(
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    total = db.query(StaffUser).count()
    staff = db.query(StaffUser).offset(skip).limit(limit).all()
    return PaginatedResponse[StaffRead](items=staff, pagination=build_pagination_meta(skip, limit, total))


@router.get("/{staff_id}", response_model=StaffRead)
def read_staff(
    staff_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    staff = db.get(StaffUser, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff account not found")
    return staff


@router.post("", response_model=StaffRead)
def create_staff(
    staff: StaffCreate,
    background_tasks: BackgroundTasks,
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
        # New accounts always start on a temporary password (whatever the
        # creator typed into the form) - forces a real password choice
        # before the dashboard is usable at all. Cleared by
        # change_my_password once the new hire actually sets their own.
        must_change_password=True,
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

    # staff.password is the PLAINTEXT the creator just typed - only
    # available here, before it's gone for good behind password_hash.
    # Enqueued the same way every other email in this project is (see
    # jobs.py) - runs after this response is sent, doesn't block the
    # creator waiting on it.
    background_tasks.add_task(
        send_staff_welcome_email, new_staff.email, new_staff.full_name, staff.password, new_staff.role.value
    )
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


@router.post("/{staff_id}/reset-password", response_model=StaffPasswordResetResult)
def reset_staff_password(
    staff_id: uuid.UUID,
    current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    # Not in the original spec - see StaffPasswordResetResult's comment
    # for why this exists (a locked-out staff member had no recovery
    # route at all before this). token_urlsafe generates a long,
    # cryptographically random string - the same generator
    # security.generate_refresh_token already uses for refresh tokens,
    # reused here since it's exactly the "unguessable random string"
    # property a temporary password needs too.
    existing = db.get(StaffUser, staff_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Staff account not found")

    temporary_password = secrets.token_urlsafe(9)
    existing.password_hash = hash_password(temporary_password)
    # An admin-issued temporary password needs the same forced-reset
    # treatment a brand-new account gets - the previous password no longer
    # works, so whoever logs in with this one must be the account owner
    # setting a real password of their own before continuing.
    existing.must_change_password = True
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="staff.password_reset",
        resource_type="staff_user",
        resource_id=existing.id,
        # No previous_value/new_value - the whole point is not persisting
        # either the old or new password anywhere, even hashed context.
    )
    db.commit()
    return StaffPasswordResetResult(temporary_password=temporary_password)


@router.patch("/{staff_id}/status", response_model=StaffRead)
def set_staff_status(
    staff_id: uuid.UUID,
    update: StaffStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(*MANAGE_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    # Sets is_active to whatever the caller asked for, rather than
    # blindly flipping it - a toggle isn't idempotent (a double-click, a
    # retried request, or two staff acting on the same account at once can
    # all silently reverse the intended change instead of erroring or
    # no-op-ing). Setting an explicit value means a repeat of the exact
    # same request is always safe.
    existing = db.get(StaffUser, staff_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Staff account not found")

    existing.is_active = update.is_active
    db.commit()
    db.refresh(existing)
    return existing
