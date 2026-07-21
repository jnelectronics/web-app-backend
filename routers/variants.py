# All the HTTP routes for the "product variants" domain live here.
# Same CRUD shape as the other routers, plus a product_id filter on the
# list endpoint since variants are almost always browsed per-product, and
# attributes (an EAV table - see models.py's VariantAttribute) which get
# built into a plain dict for the response rather than modeled as a
# SQLAlchemy relationship.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Product, ProductVariant, StaffRole, StaffUser, VariantAttribute
from schemas import VariantCreate, VariantRead
from security import require_staff_role

router = APIRouter(prefix="/variants", tags=["variants"], route_class=EnvelopeRoute)


def _build_variant_read(variant: ProductVariant, db: Session) -> VariantRead:
    # Shared by every route below - re-reads this variant's attributes
    # fresh from the database, same pattern as cart.py's _build_cart_read.
    attribute_rows = db.query(VariantAttribute).filter(VariantAttribute.variant_id == variant.id).all()
    return VariantRead(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        variant_label=variant.variant_label,
        price=variant.price,
        is_active=variant.is_active,
        created_at=variant.created_at,
        updated_at=variant.updated_at,
        attributes={row.attribute_name: row.attribute_value for row in attribute_rows},
    )


def _replace_attributes(variant_id: uuid.UUID, attributes: dict[str, str] | None, db: Session) -> None:
    # Used on both create and update - wipes whatever attribute rows exist
    # for this variant and writes the new set, rather than trying to diff
    # old vs new key-by-key. Simple and correct for how small this set
    # always is (a handful of attributes per variant, not hundreds).
    db.query(VariantAttribute).filter(VariantAttribute.variant_id == variant_id).delete()
    for name, value in (attributes or {}).items():
        db.add(VariantAttribute(variant_id=variant_id, attribute_name=name, attribute_value=value))


@router.get("/{variant_id}", response_model=VariantRead)
def read_variant(variant_id: uuid.UUID, db: Session = Depends(get_db)):
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return _build_variant_read(variant, db)


@router.get("", response_model=list[VariantRead])
def list_variants(
    product_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    query = db.query(ProductVariant).filter(ProductVariant.is_active == True)  # noqa: E712

    # Optional filter: GET /variants?product_id=... narrows the list down
    # to just one product's variants. Without it, this lists across all products.
    if product_id is not None:
        query = query.filter(ProductVariant.product_id == product_id)

    variants = query.offset(skip).limit(limit).all()
    return [_build_variant_read(v, db) for v in variants]


@router.post("", response_model=VariantRead)
def create_variant(
    variant: VariantCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Same reasoning as products checking their category: reject a variant
    # for a product that doesn't exist with a clean 404, not a raw DB error.
    if db.get(Product, variant.product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    new_variant = ProductVariant(
        product_id=variant.product_id,
        sku=variant.sku,
        variant_label=variant.variant_label,
        price=variant.price,
    )
    db.add(new_variant)
    db.flush()  # assigns new_variant.id so attribute rows can reference it
    _replace_attributes(new_variant.id, variant.attributes, db)
    db.commit()
    db.refresh(new_variant)
    return _build_variant_read(new_variant, db)


@router.put("/{variant_id}", response_model=VariantRead)
def update_variant(
    variant_id: uuid.UUID,
    variant: VariantCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(ProductVariant, variant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    if db.get(Product, variant.product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    existing.product_id = variant.product_id
    existing.sku = variant.sku
    existing.variant_label = variant.variant_label
    existing.price = variant.price
    _replace_attributes(existing.id, variant.attributes, db)
    db.commit()
    db.refresh(existing)
    return _build_variant_read(existing, db)


# Soft-delete, same reasoning as the other domains: never actually remove the row.
@router.delete("/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    variant_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(ProductVariant, variant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    existing.is_active = False
    db.commit()
