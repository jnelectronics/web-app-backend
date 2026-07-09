import uuid

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditLogRepository


class AuditService:
    """FR-AUDIT-001-005: called by other modules' services after a mutating staff action."""

    def __init__(self, db: Session) -> None:
        self.repository = AuditLogRepository(db)

    def record(
        self,
        *,
        staff_user_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        previous_value: dict | None = None,
        new_value: dict | None = None,
    ) -> AuditLog:
        return self.repository.create(
            AuditLog(
                staff_user_id=staff_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                previous_value=previous_value,
                new_value=new_value,
            )
        )

    def list_recent(self, *, page: int, page_size: int) -> tuple[list[AuditLog], int]:
        offset = (page - 1) * page_size
        return self.repository.list(offset=offset, limit=page_size), self.repository.count()
