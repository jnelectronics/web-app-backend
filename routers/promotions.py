# Promotions: homepage banners, per-product discount windows, and (new)
# the promotion LIBRARY staff configure once and apply to products.
# Three routers in one file - banner_router (/banners), discount_router
# (/products/.../discounts and .../apply-promotion, since those are
# nested under a product), and promotion_router (/promotions, the library
# itself) - same "different prefixes, one closely-related file" reasoning
# as routers/categories.py's router + group_router.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from models import Banner, Product, ProductDiscount, Promotion, StaffRole, StaffUser
from schemas import (
    ApplyPromotionRequest,
    BannerCreate,
    BannerRead,
    BannerStatusUpdate,
    ProductDiscountCreate,
    ProductDiscountRead,
    ProductDiscountStatusUpdate,
    ProductRead,
    PromotionCreate,
    PromotionRead,
    PromotionStatusUpdate,
)
from security import decode_token_claims, require_staff_role

banner_router = APIRouter(prefix="/banners", tags=["promotions"], route_class=EnvelopeRoute)
discount_router = APIRouter(prefix="/products", tags=["promotions"], route_class=EnvelopeRoute)
promotion_router = APIRouter(prefix="/promotions", tags=["promotions"], route_class=EnvelopeRoute)

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
    # System Administrator bypasses this check the same way it bypasses
    # require_staff_role() in security.py - it's a true superset role,
    # not just another name on this specific allow-list.
    if staff.role not in (StaffRole.INVENTORY_MANAGER, StaffRole.SYSTEM_ADMINISTRATOR):
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


@discount_router.post("/{product_id}/apply-promotion", response_model=ProductRead)
def apply_promotion_to_product(
    product_id: uuid.UUID,
    request: ApplyPromotionRequest,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Imported here (not at module level) to avoid a circular import -
    # routers/products.py never imports this file, so this direction is
    # safe, but keeping it local makes that one-way relationship obvious
    # at the call site instead of hidden in the top-of-file import block.
    from routers.products import _build_product_read

    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    promotion = db.get(Promotion, request.promotion_id)
    if promotion is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    if not promotion.is_active:
        raise HTTPException(status_code=422, detail="This promotion is not active")

    # Deactivate whatever promotion-linked discount is currently applied
    # (if any) BEFORE creating the new one - otherwise switching a product
    # from one promotion to another would leave both simultaneously
    # "active" in product_discounts, and _is_product_discounted/cart price
    # resolution would have two conflicting windows to choose between.
    previous_discount = (
        db.query(ProductDiscount)
        .filter(
            ProductDiscount.product_id == product_id,
            ProductDiscount.promotion_id.isnot(None),
            ProductDiscount.is_active == True,  # noqa: E712
        )
        .first()
    )
    if previous_discount is not None:
        previous_discount.is_active = False

    new_discount = ProductDiscount(
        product_id=product_id,
        promotion_id=promotion.id,
        discount_type=promotion.discount_type,
        discount_value=promotion.discount_value,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
    )
    db.add(new_discount)

    previous_value = {"applied_promotion_id": str(product.applied_promotion_id) if product.applied_promotion_id else None}
    product.applied_promotion_id = promotion.id
    # Applying a promotion IS the act of putting a product on sale - see
    # schemas.ProductCreate's own comment on is_on_sale for why the
    # product write endpoints don't accept this combination directly.
    product.is_on_sale = True
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.apply_promotion",
        resource_type="product",
        resource_id=product.id,
        previous_value=previous_value,
        new_value={"applied_promotion_id": str(promotion.id)},
    )
    db.commit()
    db.refresh(product)
    return _build_product_read(product, db)


@discount_router.delete("/{product_id}/apply-promotion", response_model=ProductRead)
def clear_promotion_from_product(
    product_id: uuid.UUID,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    from routers.products import _build_product_read

    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # Idempotent - clearing an already-empty promotion link isn't an error,
    # same "caller states the end result" reasoning as the *StatusUpdate
    # endpoints elsewhere in this project.
    if product.applied_promotion_id is not None:
        active_discount = (
            db.query(ProductDiscount)
            .filter(
                ProductDiscount.product_id == product_id,
                ProductDiscount.promotion_id == product.applied_promotion_id,
                ProductDiscount.is_active == True,  # noqa: E712
            )
            .first()
        )
        if active_discount is not None:
            active_discount.is_active = False

        previous_value = {"applied_promotion_id": str(product.applied_promotion_id)}
        product.applied_promotion_id = None
        product.is_on_sale = False
        write_audit_log(
            db,
            staff_user_id=current_staff.id,
            action="product.clear_promotion",
            resource_type="product",
            resource_id=product.id,
            previous_value=previous_value,
            new_value={"applied_promotion_id": None},
        )
        db.commit()
        db.refresh(product)

    return _build_product_read(product, db)


@promotion_router.get("", response_model=list[PromotionRead])
def list_promotions(
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # No public view at all - unlike banners, the promotion library is
    # purely an internal staff tool (the picker on the product form);
    # customers only ever see its EFFECT (a discounted price), never the
    # library itself.
    return db.query(Promotion).order_by(Promotion.created_at.desc()).all()


@promotion_router.get("/{promotion_id}", response_model=PromotionRead)
def read_promotion(
    promotion_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    promotion = db.get(Promotion, promotion_id)
    if promotion is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return promotion


@promotion_router.post("", response_model=PromotionRead)
def create_promotion(
    promotion: PromotionCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    new_promotion = Promotion(
        name=promotion.name,
        discount_type=promotion.discount_type,
        discount_value=promotion.discount_value,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
    )
    db.add(new_promotion)
    db.commit()
    db.refresh(new_promotion)
    return new_promotion


@promotion_router.put("/{promotion_id}", response_model=PromotionRead)
def update_promotion(
    promotion_id: uuid.UUID,
    promotion: PromotionCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(Promotion, promotion_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Promotion not found")

    existing.name = promotion.name
    existing.discount_type = promotion.discount_type
    existing.discount_value = promotion.discount_value
    existing.starts_at = promotion.starts_at
    existing.ends_at = promotion.ends_at
    db.commit()
    db.refresh(existing)
    return existing


@promotion_router.patch("/{promotion_id}/status", response_model=PromotionRead)
def set_promotion_status(
    promotion_id: uuid.UUID,
    update: PromotionStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Sets rather than toggles - see routers/staff.py's set_staff_status
    # for the idempotency reasoning.
    existing = db.get(Promotion, promotion_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Promotion not found")

    existing.is_active = update.is_active
    db.commit()
    db.refresh(existing)
    return existing
