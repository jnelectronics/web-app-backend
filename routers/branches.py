# All the HTTP routes for the "branches" domain live here.
# Same pattern as routers/categories.py - no foreign keys to check here
# since branches don't reference any other table.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Branch, StaffRole, StaffUser
from schemas import BranchCreate, BranchRead
from security import get_current_staff, require_staff_role

router = APIRouter(prefix="/branches", tags=["branches"], route_class=EnvelopeRoute)


@router.get("/{branch_id}", response_model=BranchRead)
def read_branch(
    branch_id: uuid.UUID,
    # get_current_staff (not require_staff_role) - per the docs, branch
    # visibility is just "Staff", any role, unlike the Owner/Sales
    # Attendant-only writes below. Branches are never shown to customers at all
    # (FR-BRANCH-007), so there's no public path here like categories/products.
    _current_staff: StaffUser = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    branch = db.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


@router.get("", response_model=list[BranchRead])
def list_branches(
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    return (
        db.query(Branch)
        .filter(Branch.is_active == True)  # noqa: E712
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("", response_model=BranchRead)
def create_branch(
    branch: BranchCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    new_branch = Branch(
        name=branch.name,
        address=branch.address,
        phone_number=branch.phone_number,
        email=branch.email,
    )
    db.add(new_branch)
    db.commit()
    db.refresh(new_branch)
    return new_branch


@router.put("/{branch_id}", response_model=BranchRead)
def update_branch(
    branch_id: uuid.UUID,
    branch: BranchCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Branch, branch_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    existing.name = branch.name
    existing.address = branch.address
    existing.phone_number = branch.phone_number
    existing.email = branch.email
    db.commit()
    db.refresh(existing)
    return existing


# Soft-delete, same reasoning as categories/products: never actually remove the row.
@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_branch(
    branch_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Branch, branch_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    existing.is_active = False
    db.commit()


@router.patch("/{branch_id}/set-default", response_model=BranchRead)
def set_default_branch(
    branch_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # Same "clear the old one first, then set the new one" shape as
    # routers/products.py's set_primary_product_image - the model's own
    # partial unique index (models.py's uq_branches_single_default) is
    # what actually enforces "at most one default branch," but it checks on
    # every write, so both rows being true at once - even for an instant
    # within this same transaction - would still fail.
    target = db.get(Branch, branch_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    current_default = db.query(Branch).filter(Branch.is_default == True).first()  # noqa: E712
    if current_default is not None and current_default.id != target.id:
        current_default.is_default = False
        db.flush()

    target.is_default = True
    db.commit()
    db.refresh(target)
    return target
