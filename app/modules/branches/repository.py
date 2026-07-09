from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.branches.models import Branch


class BranchRepository(BaseRepository[Branch]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Branch)
