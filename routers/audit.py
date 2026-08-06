# Read-only - per the docs, audit_logs has no POST/PATCH/DELETE at all
# (FR-AUDIT-004/005). Entries are written internally via audit.write_audit_log
# from inside the routers that make the actual change, never through this
# router.

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import AuditLog, StaffRole, StaffUser
from pagination import build_pagination_meta
from schemas import AuditLogRead, PaginatedResponse
from security import require_staff_role

router = APIRouter(prefix="/audit-logs", tags=["audit"], route_class=EnvelopeRoute)

# A different pair than most staff-gated endpoints in this project - System
# Administrator IS included here (unlike inventory/catalog), Sales
# Attendant is NOT. Matches the docs' table for this endpoint exactly.
READ_AUDIT_ROLES = (StaffRole.INVENTORY_MANAGER, StaffRole.SYSTEM_ADMINISTRATOR)


@router.get("", response_model=PaginatedResponse[AuditLogRead])
def list_audit_logs(
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    staff_user_id: uuid.UUID | None = None,
    action: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*READ_AUDIT_ROLES)),
    db: Session = Depends(get_db),
):
    # A LEFT OUTER JOIN (not inner) - an audit entry must still show up
    # even if the StaffUser it names has since disappeared (see the
    # schema's comment on staff_full_name/staff_email being nullable).
    query = db.query(AuditLog, StaffUser).outerjoin(StaffUser, AuditLog.staff_user_id == StaffUser.id)

    if resource_type is not None:
        query = query.filter(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        query = query.filter(AuditLog.resource_id == resource_id)
    if staff_user_id is not None:
        query = query.filter(AuditLog.staff_user_id == staff_user_id)
    if action is not None:
        query = query.filter(AuditLog.action == action)
    if start_date is not None:
        query = query.filter(AuditLog.created_at >= start_date)
    if end_date is not None:
        query = query.filter(AuditLog.created_at <= end_date)

    query = query.order_by(AuditLog.created_at.desc())
    total = query.count()
    rows = query.offset(skip).limit(limit).all()
    items = [
        AuditLogRead(
            id=log.id,
            staff_user_id=log.staff_user_id,
            staff_full_name=staff.full_name if staff is not None else None,
            staff_email=staff.email if staff is not None else None,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            previous_value=log.previous_value,
            new_value=log.new_value,
            created_at=log.created_at,
        )
        for log, staff in rows
    ]
    return PaginatedResponse[AuditLogRead](items=items, pagination=build_pagination_meta(skip, limit, total))
