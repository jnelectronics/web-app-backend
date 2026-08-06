# Promotions: homepage banners, and per-product discount windows. Two
# separate routers in one file (rather than a shared prefix) since the
# docs put them under different paths - /banners and
# /products/{id}/discounts - not a shared /promotions prefix.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Banner, Product, ProductDiscount, StaffRole, StaffUser
from schemas import (
    BannerCreate,
    BannerRead,
    BannerStatusUpdate,
    ProductDiscountCreate,
    ProductDiscountRead,
    ProductDiscountStatusUpdate,
)
from security import decode_token_claims, require_staff_role

banner_router = APIRouter(prefix="/banners", tags=["promotions"], route_class=EnvelopeRoute)
discount_router = APIRouter(prefix="/products", tags=["promotions"], route_class=EnvelopeRoute)

# auto_error=False - see routers/payments.py's identical comment.
# include_inactive=true needs a staff token; the default (public) view
# needs none at all, so this endpoint can't just require auth unconditionally.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


@banner_router.get("", response_model=list[BannerRead])
def list_active_banners(
    include_inactive: bool = False,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    # Default (include_inactive=False, no auth needed): "active" means both
    # the flag AND currently inside its optional scheduling window - a
    # banner with starts_at/ends_at set shouldn't show up outside that
    # window just because is_active is still true. This is the public
    # storefront view.
    if not include_inactive:
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

    # include_inactive=true is the admin banner-management view - drafts,
    # deactivated banners, and future-scheduled ones a manager needs to
    # find again to edit/reactivate. Requires the same role banner WRITES
    # already require, checked here directly (not via
    # Depends(require_staff_role(...)) on the route itself, since that
    # would make auth unconditionally required even for the public
    # default case above).
    invalid_token = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    if credentials is None:
        raise invalid_token
    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") != "staff":
        raise invalid_token
    staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
    if staff is None or not staff.is_active:
        raise invalid_token
    if staff.role != StaffRole.INVENTORY_MANAGER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")

    return db.query(Banner).order_by(Banner.display_order).all()


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
def set_banner_status(
    banner_id: uuid.UUID,
    update: BannerStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Sets rather than toggles - see routers/staff.py's set_staff_status
    # for the idempotency reasoning.
    existing = db.get(Banner, banner_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Banner not found")

    existing.is_active = update.is_active
    db.commit()
    db.refresh(existing)
    return existing


@discount_router.get("/{product_id}/discounts", response_model=list[ProductDiscountRead])
def list_product_discounts(product_id: uuid.UUID, db: Session = Depends(get_db)):
    # Public (no auth) - the storefront needs this to show a sale price/
    # strikethrough/badge, and the admin promotions screen needs it to
    # list what's currently configured before a manager can edit it.
    # Returns ALL discounts for this product (past, current, future, and
    # deactivated), not just the currently-active one - routers/products.py's
    # _is_product_discounted is the one that answers "is it discounted
    # RIGHT NOW"; this endpoint is the full history/schedule view.
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return (
        db.query(ProductDiscount)
        .filter(ProductDiscount.product_id == product_id)
        .order_by(ProductDiscount.created_at.desc())
        .all()
    )


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
def set_product_discount_status(
    product_id: uuid.UUID,
    discount_id: uuid.UUID,
    update: ProductDiscountStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Sets rather than toggles - see routers/staff.py's set_staff_status
    # for the idempotency reasoning.
    existing = (
        db.query(ProductDiscount)
        .filter(ProductDiscount.id == discount_id, ProductDiscount.product_id == product_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Discount not found")

    existing.is_active = update.is_active
    db.commit()
    db.refresh(existing)
    return existing
