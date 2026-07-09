import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.products.schemas import (
    ProductCreate,
    ProductRead,
    ProductStatusUpdate,
    ProductUpdate,
    ProductVariantCreate,
    ProductVariantRead,
)
from app.modules.products.service import ProductService
from app.utils.responses import paginated_envelope, success_envelope

router = APIRouter(prefix="/products", tags=["Products"])

_manage_roles = require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)


@router.get("")
def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    products, total = ProductService(db).list_products(page=page, page_size=page_size)
    return paginated_envelope(
        [ProductRead.model_validate(p) for p in products],
        page=page,
        page_size=page_size,
        total_records=total,
        message="Products retrieved successfully.",
    )


@router.get("/{product_uuid}")
def get_product(product_uuid: uuid.UUID, db: Session = Depends(get_db_session)):
    product = ProductService(db).get_product(product_uuid)
    return success_envelope(ProductRead.model_validate(product), "Product retrieved successfully.")


@router.post("", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate, principal=Depends(_manage_roles), db: Session = Depends(get_db_session)
):
    product = ProductService(db).create_product(payload)
    return success_envelope(ProductRead.model_validate(product), "Product created successfully.")


@router.patch("/{product_uuid}")
def update_product(
    product_uuid: uuid.UUID,
    payload: ProductUpdate,
    principal=Depends(_manage_roles),
    db: Session = Depends(get_db_session),
):
    product = ProductService(db).update_product(product_uuid, payload)
    return success_envelope(ProductRead.model_validate(product), "Product updated successfully.")


@router.patch("/{product_uuid}/status")
def set_product_status(
    product_uuid: uuid.UUID,
    payload: ProductStatusUpdate,
    principal=Depends(_manage_roles),
    db: Session = Depends(get_db_session),
):
    product = ProductService(db).set_status(product_uuid, payload)
    return success_envelope(ProductRead.model_validate(product), "Product status updated successfully.")


@router.post("/{product_uuid}/variants", status_code=status.HTTP_201_CREATED)
def add_variant(
    product_uuid: uuid.UUID,
    payload: ProductVariantCreate,
    principal=Depends(_manage_roles),
    db: Session = Depends(get_db_session),
):
    variant = ProductService(db).add_variant(product_uuid, payload)
    return success_envelope(ProductVariantRead.model_validate(variant), "Variant added successfully.")


# TODO: PATCH /{product_uuid}/variants/{variant_uuid}(/status), image endpoints (§5.4).
