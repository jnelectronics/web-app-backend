import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.branches.schemas import BranchCreate, BranchRead, BranchStatusUpdate, BranchUpdate
from app.modules.branches.service import BranchService
from app.utils.responses import paginated_envelope, success_envelope

router = APIRouter(prefix="/branches", tags=["Branches"])


@router.get("")
def list_branches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    branches, total = BranchService(db).list_branches(page=page, page_size=page_size)
    return paginated_envelope(
        [BranchRead.model_validate(b) for b in branches],
        page=page,
        page_size=page_size,
        total_records=total,
        message="Branches retrieved successfully.",
    )


@router.get("/{branch_uuid}")
def get_branch(branch_uuid: uuid.UUID, db: Session = Depends(get_db_session)):
    branch = BranchService(db).get_branch(branch_uuid)
    return success_envelope(BranchRead.model_validate(branch), "Branch retrieved successfully.")


@router.post("", status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchCreate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    branch = BranchService(db).create_branch(payload)
    return success_envelope(BranchRead.model_validate(branch), "Branch created successfully.")


@router.patch("/{branch_uuid}")
def update_branch(
    branch_uuid: uuid.UUID,
    payload: BranchUpdate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    branch = BranchService(db).update_branch(branch_uuid, payload)
    return success_envelope(BranchRead.model_validate(branch), "Branch updated successfully.")


@router.patch("/{branch_uuid}/status")
def set_branch_status(
    branch_uuid: uuid.UUID,
    payload: BranchStatusUpdate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    branch = BranchService(db).set_status(branch_uuid, payload)
    return success_envelope(BranchRead.model_validate(branch), "Branch status updated successfully.")
