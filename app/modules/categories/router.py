import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.categories.schemas import (
    CategoryCreate,
    CategoryRead,
    CategoryStatusUpdate,
    CategoryUpdate,
)
from app.modules.categories.service import CategoryService
from app.utils.responses import paginated_envelope, success_envelope

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("")
def list_categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    categories, total = CategoryService(db).list_categories(page=page, page_size=page_size)
    return paginated_envelope(
        [CategoryRead.model_validate(c) for c in categories],
        page=page,
        page_size=page_size,
        total_records=total,
        message="Categories retrieved successfully.",
    )


@router.get("/{category_uuid}")
def get_category(category_uuid: uuid.UUID, db: Session = Depends(get_db_session)):
    category = CategoryService(db).get_category(category_uuid)
    return success_envelope(CategoryRead.model_validate(category), "Category retrieved successfully.")


@router.post("", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    category = CategoryService(db).create_category(payload)
    return success_envelope(CategoryRead.model_validate(category), "Category created successfully.")


@router.patch("/{category_uuid}")
def update_category(
    category_uuid: uuid.UUID,
    payload: CategoryUpdate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    category = CategoryService(db).update_category(category_uuid, payload)
    return success_envelope(CategoryRead.model_validate(category), "Category updated successfully.")


@router.patch("/{category_uuid}/status")
def set_category_status(
    category_uuid: uuid.UUID,
    payload: CategoryStatusUpdate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)),
    db: Session = Depends(get_db_session),
):
    category = CategoryService(db).set_status(category_uuid, payload)
    return success_envelope(CategoryRead.model_validate(category), "Category status updated successfully.")
