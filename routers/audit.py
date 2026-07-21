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
from schemas import AuditLogRead
from security import require_staff_role

router = APIRouter(prefix="/audit-logs", tags=["audit"], route_class=EnvelopeRoute)

# A different pair than most staff-gated endpoints in this project - System
# Administrator IS included here (unlike inventory/catalog), Sales
# Attendant is NOT. Matches the docs' table for this endpoint exactly.
READ_AUDIT_ROLES = (StaffRole.INVENTORY_MANAGER, StaffRole.SYSTEM_ADMINISTRATOR)


@router.get("", response_model=list[AuditLogRead])
def list_audit_logs(
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    staff_user_id: uuid.UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*READ_AUDIT_ROLES)),
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)

    if resource_type is not None:
        query = query.filter(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        query = query.filter(AuditLog.resource_id == resource_id)
    if staff_user_id is not None:
        query = query.filter(AuditLog.staff_user_id == staff_user_id)
    if start_date is not None:
        query = query.filter(AuditLog.created_at >= start_date)
    if end_date is not None:
        query = query.filter(AuditLog.created_at <= end_date)

    return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
