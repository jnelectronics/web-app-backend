import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.modules.branches.models import Branch
from app.modules.branches.repository import BranchRepository
from app.modules.branches.schemas import BranchCreate, BranchStatusUpdate, BranchUpdate


class BranchService:
    def __init__(self, db: Session) -> None:
        self.repository = BranchRepository(db)

    def list_branches(self, *, page: int, page_size: int) -> tuple[list[Branch], int]:
        offset = (page - 1) * page_size
        return self.repository.list(offset=offset, limit=page_size), self.repository.count()

    def get_branch(self, branch_id: uuid.UUID) -> Branch:
        branch = self.repository.get(branch_id)
        if not branch:
            raise NotFoundError("Branch not found.")
        return branch

    def create_branch(self, payload: BranchCreate) -> Branch:
        return self.repository.create(Branch(**payload.model_dump()))

    def update_branch(self, branch_id: uuid.UUID, payload: BranchUpdate) -> Branch:
        branch = self.get_branch(branch_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(branch, field, value)
        return self.repository.save(branch)

    def set_status(self, branch_id: uuid.UUID, payload: BranchStatusUpdate) -> Branch:
        branch = self.get_branch(branch_id)
        branch.is_active = payload.is_active
        return self.repository.save(branch)
