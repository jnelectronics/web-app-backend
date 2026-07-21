# Promotions: homepage banners, and per-product discount windows. Two
# separate routers in one file (rather than a shared prefix) since the
# docs put them under different paths - /banners and
# /products/{id}/discounts - not a shared /promotions prefix.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Banner, Product, ProductDiscount, StaffRole, StaffUser
from schemas import BannerCreate, BannerRead, ProductDiscountCreate, ProductDiscountRead
from security import require_staff_role

banner_router = APIRouter(prefix="/banners", tags=["promotions"], route_class=EnvelopeRoute)
discount_router = APIRouter(prefix="/products", tags=["promotions"], route_class=EnvelopeRoute)


@banner_router.get("", response_model=list[BannerRead])
def list_active_banners(db: Session = Depends(get_db)):
    # "Active" means both the flag AND currently inside its optional
    # scheduling window - a banner with starts_at/ends_at set shouldn't
    # show up outside that window just because is_active is still true.
    now = datetime.now(timezone.utc)
    return (
        db.query(Banner)
        .filter(
            Banner.is_active == True,  # noqa: E712
            (Banner.starts_at.is_(None)) | (Banner.starts_at <= now),
            (Banner.ends_at.is_(None)) | (Banner.ends_at >= now),
        )
        .order_by(Banner.display_order)
        .all()
    )


@banner_router.post("", response_model=BannerRead)
def create_banner(
    banner: BannerCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    new_banner = Banner(
        title=banner.title,
        image_url=banner.image_url,
        display_order=banner.display_order,
        starts_at=banner.starts_at,
        ends_at=banner.ends_at,
    )
    db.add(new_banner)
    db.commit()
    db.refresh(new_banner)
    return new_banner


@banner_router.put("/{banner_id}", response_model=BannerRead)
def update_banner(
    banner_id: uuid.UUID,
    banner: BannerCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(Banner, banner_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Banner not found")

    existing.title = banner.title
    existing.image_url = banner.image_url
    existing.display_order = banner.display_order
    existing.starts_at = banner.starts_at
    existing.ends_at = banner.ends_at
    db.commit()
    db.refresh(existing)
    return existing


@banner_router.patch("/{banner_id}/status", response_model=BannerRead)
def toggle_banner_status(
    banner_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(Banner, banner_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Banner not found")

    existing.is_active = not existing.is_active
    db.commit()
    db.refresh(existing)
    return existing


@discount_router.post("/{product_id}/discounts", response_model=ProductDiscountRead)
def create_product_discount(
    product_id: uuid.UUID,
    discount: ProductDiscountCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    new_discount = ProductDiscount(
        product_id=product_id,
        discount_type=discount.discount_type,
        discount_value=discount.discount_value,
        starts_at=discount.starts_at,
        ends_at=discount.ends_at,
    )
    db.add(new_discount)
    db.commit()
    db.refresh(new_discount)
    return new_discount


@discount_router.put("/{product_id}/discounts/{discount_id}", response_model=ProductDiscountRead)
def update_product_discount(
    product_id: uuid.UUID,
    discount_id: uuid.UUID,
    discount: ProductDiscountCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(ProductDiscount)
        .filter(ProductDiscount.id == discount_id, ProductDiscount.product_id == product_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Discount not found")

    existing.discount_type = discount.discount_type
    existing.discount_value = discount.discount_value
    existing.starts_at = discount.starts_at
    existing.ends_at = discount.ends_at
    db.commit()
    db.refresh(existing)
    return existing


@discount_router.patch("/{product_id}/discounts/{discount_id}/status", response_model=ProductDiscountRead)
def toggle_product_discount_status(
    product_id: uuid.UUID,
    discount_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Deactivate-only in practice (BR-PROMO-002), but a toggle - same
    # reasoning as every other status-flip endpoint in this codebase - is
    # simpler than a dedicated "deactivate" verb and just as correct, since
    # staff can always toggle a discount back on if turned off by mistake.
    existing = (
        db.query(ProductDiscount)
        .filter(ProductDiscount.id == discount_id, ProductDiscount.product_id == product_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Discount not found")

    existing.is_active = not existing.is_active
    db.commit()
    db.refresh(existing)
    return existing
