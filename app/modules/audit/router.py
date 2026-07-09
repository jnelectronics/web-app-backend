from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.audit.schemas import AuditLogRead
from app.modules.audit.service import AuditService
from app.utils.responses import paginated_envelope

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("")
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal=Depends(require_roles(StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    logs, total = AuditService(db).list_recent(page=page, page_size=page_size)
    return paginated_envelope(
        [AuditLogRead.model_validate(log) for log in logs],
        page=page,
        page_size=page_size,
        total_records=total,
        message="Audit logs retrieved successfully.",
    )
