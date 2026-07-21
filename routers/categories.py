# All the HTTP routes for the "categories" domain live here.
# Same pattern as routers/products.py - if that one made sense, this is
# the same shape applied to a different table.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Category, StaffRole, StaffUser
from schemas import CategoryCreate, CategoryRead
from security import require_staff_role

router = APIRouter(prefix="/categories", tags=["categories"], route_class=EnvelopeRoute)


@router.get("/{category_id}", response_model=CategoryRead)
def read_category(category_id: uuid.UUID, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.get("", response_model=list[CategoryRead])
def list_categories(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return (
        db.query(Category)
        .filter(Category.is_active == True)  # noqa: E712
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("", response_model=CategoryRead)
def create_category(
    category: CategoryCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    new_category = Category(name=category.name, description=category.description)
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    return new_category


@router.put("/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: uuid.UUID,
    category: CategoryCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(Category, category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")

    existing.name = category.name
    existing.description = category.description
    db.commit()
    db.refresh(existing)
    return existing


# Soft-delete, same reasoning as products: never actually remove the row.
@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(Category, category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")

    existing.is_active = False
    db.commit()
