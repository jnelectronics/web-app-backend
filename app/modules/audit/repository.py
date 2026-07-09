from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.audit.models import AuditLog


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, AuditLog)
