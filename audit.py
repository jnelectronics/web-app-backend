# A small helper for writing audit_logs rows (FR-AUDIT-001-005). Lives at
# the top level (not inside a router) since multiple routers across
# different domains need it - same reasoning as database.py's get_db.

import uuid

from sqlalchemy.orm import Session

from models import AuditLog


def write_audit_log(
    db: Session,
    staff_user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    previous_value: dict | None = None,
    new_value: dict | None = None,
) -> None:
    # Deliberately does NOT call db.commit() itself - the caller adds this
    # to whatever transaction it's already building (e.g. alongside the
    # actual product update), so the audit entry and the change it
    # describes always succeed or fail together, never one without the other.
    db.add(
        AuditLog(
            staff_user_id=staff_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            previous_value=previous_value,
            new_value=new_value,
        )
    )
