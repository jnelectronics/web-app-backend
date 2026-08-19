# All the HTTP routes for the "product variants" domain live here.
# Same CRUD shape as the other routers, plus a product_id filter on the
# list endpoint since variants are almost always browsed per-product, and
# attributes (an EAV table - see models.py's VariantAttribute) which get
# built into a plain dict for the response rather than modeled as a
# SQLAlchemy relationship.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Branch, InventoryMovement, InventoryRecord, MovementType, Product, ProductVariant, StaffRole, StaffUser, VariantAttribute
from pagination import build_pagination_meta
from schemas import PaginatedResponse, SkuAvailability, VariantCreate, VariantRead, VariantStockUpdate
from security import decode_token_claims, require_staff_role

router = APIRouter(prefix="/variants", tags=["variants"], route_class=EnvelopeRoute)

# Same "optional auth" trick used by routers/categories.py's group listing
# and routers/promotions.py's banner listing - the storefront product page
# calls these same GET routes with no token at all, but a staff caller
# (the admin edit-product form) needs quantity_available included, so the
# routes can't require a token unconditionally.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


def _current_staff_or_none(
    credentials: HTTPAuthorizationCredentials | None, db: Session
) -> StaffUser | None:
    if credentials is None:
        return None
    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") != "staff":
        return None
    staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
    if staff is None or not staff.is_active:
        return None
    return staff


def _build_variant_read(variant: ProductVariant, db: Session, *, requesting_staff: StaffUser | None) -> VariantRead:
    # Shared by every route below - re-reads this variant's attributes
    # fresh from the database, same pattern as cart.py's _build_cart_read.
    attribute_rows = db.query(VariantAttribute).filter(VariantAttribute.variant_id == variant.id).all()
    # ANY branch with stock > 0 makes this true - a plain existence check,
    # not a sum, since a customer only needs to know "can I buy this
    # somewhere", not the total quantity across every branch.
    in_stock = (
        db.query(InventoryRecord)
        .filter(InventoryRecord.variant_id == variant.id, InventoryRecord.quantity_available > 0)
        .first()
        is not None
    )

    # quantity_available: a REAL number, so only ever computed for a
    # staff-authenticated caller (see schemas.VariantRead's own comment on
    # this field for why) - stays None for the public storefront call.
    quantity_available = None
    if requesting_staff is not None:
        default_branch = db.query(Branch).filter(Branch.is_default == True).first()  # noqa: E712
        if default_branch is not None:
            record = (
                db.query(InventoryRecord)
                .filter(InventoryRecord.variant_id == variant.id, InventoryRecord.branch_id == default_branch.id)
                .first()
            )
            # No record yet just means "never stocked" - 0, not unknown.
            quantity_available = record.quantity_available if record is not None else 0

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
        in_stock=in_stock,
        quantity_available=quantity_available,
    )


def _replace_attributes(variant_id: uuid.UUID, attributes: dict[str, str] | None, db: Session) -> None:
    # Used on both create and update - wipes whatever attribute rows exist
    # for this variant and writes the new set, rather than trying to diff
    # old vs new key-by-key. Simple and correct for how small this set
    # always is (a handful of attributes per variant, not hundreds).
    db.query(VariantAttribute).filter(VariantAttribute.variant_id == variant_id).delete()
    for name, value in (attributes or {}).items():
        db.add(VariantAttribute(variant_id=variant_id, attribute_name=name, attribute_value=value))


# Registered BEFORE /{variant_id} below - same routing gotcha CLAUDE.md
# documents for payments.py's webhook: Starlette matches routes by raw
# path template, in registration order, BEFORE FastAPI validates a typed
# path parameter. If /{variant_id} were registered first, a request for
# /variants/check-sku would match THAT route first (with variant_id
# literally set to the string "check-sku"), fail UUID validation, and
# never reach this handler at all.
@router.get("/check-sku", response_model=SkuAvailability)
def check_sku(
    sku: str,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # Staff-only (same roles as create_variant/update_variant) - this only
    # exists to serve the admin product form's SKU generator, which used
    # to load up to 500 products client-side just to self-check for a
    # collision (see the frontend audit notes this replaces). No
    # is_active filter - ProductVariant.sku's unique=True constraint in
    # models.py applies to EVERY row regardless of is_active, so a
    # soft-deleted variant's old SKU is still taken as far as the database
    # is concerned.
    exists = db.query(ProductVariant).filter(ProductVariant.sku == sku).first() is not None
    return SkuAvailability(sku=sku, exists=exists)


@router.get("/{variant_id}", response_model=VariantRead)
def read_variant(
    variant_id: uuid.UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return _build_variant_read(variant, db, requesting_staff=_current_staff_or_none(credentials, db))


@router.get("", response_model=PaginatedResponse[VariantRead])
def list_variants(
    product_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 10,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    query = db.query(ProductVariant).filter(ProductVariant.is_active == True)  # noqa: E712

    # Optional filter: GET /variants?product_id=... narrows the list down
    # to just one product's variants. Without it, this lists across all products.
    if product_id is not None:
        query = query.filter(ProductVariant.product_id == product_id)

    total = query.count()
    variants = query.offset(skip).limit(limit).all()
    requesting_staff = _current_staff_or_none(credentials, db)
    return PaginatedResponse[VariantRead](
        items=[_build_variant_read(v, db, requesting_staff=requesting_staff) for v in variants],
        pagination=build_pagination_meta(skip, limit, total),
    )


@router.post("", response_model=VariantRead)
def create_variant(
    variant: VariantCreate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
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
    try:
        db.flush()  # assigns new_variant.id so attribute rows can reference it
    except IntegrityError:
        # Hits ProductVariant.sku's own unique=True constraint - same
        # "pre-check would need a second query anyway, so just catch the
        # constraint instead" reasoning as create_inventory_record's
        # (variant_id, branch_id) check in routers/inventory.py.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A variant with this SKU already exists")

    _replace_attributes(new_variant.id, variant.attributes, db)
    db.commit()
    db.refresh(new_variant)
    return _build_variant_read(new_variant, db, requesting_staff=current_staff)


@router.put("/{variant_id}", response_model=VariantRead)
def update_variant(
    variant_id: uuid.UUID,
    variant: VariantCreate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
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
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A variant with this SKU already exists")

    _replace_attributes(existing.id, variant.attributes, db)
    db.commit()
    db.refresh(existing)
    return _build_variant_read(existing, db, requesting_staff=current_staff)


# Soft-delete, same reasoning as the other domains: never actually remove the row.
@router.delete("/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    variant_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(ProductVariant, variant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    existing.is_active = False
    db.commit()


@router.put("/{variant_id}/stock", response_model=VariantRead)
def set_variant_stock(
    variant_id: uuid.UUID,
    update: VariantStockUpdate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # The branch-less "quantity" field on the admin product form - see
    # models.py's Branch.is_default. Always resolves against whichever ONE
    # branch is flagged default; multi-branch stock management still goes
    # through the real per-branch /inventory endpoints, untouched by this.
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    if update.quantity_available < 0:
        raise HTTPException(status_code=422, detail="quantity_available cannot be negative")

    default_branch = db.query(Branch).filter(Branch.is_default == True).first()  # noqa: E712
    if default_branch is None:
        # Only reachable if every branch's is_default got manually cleared -
        # the migration that added this column auto-flagged one branch, and
        # routers/branches.py's set_default_branch always clears-then-sets
        # rather than ever leaving zero. Still worth a clean error over a
        # confusing "NoneType has no attribute id" crash.
        raise HTTPException(status_code=422, detail="No default branch is configured")

    record = (
        db.query(InventoryRecord)
        .filter(InventoryRecord.variant_id == variant_id, InventoryRecord.branch_id == default_branch.id)
        .first()
    )

    if record is None:
        # First time this variant has ever been stocked - create the row
        # rather than requiring a separate POST /inventory call first,
        # which is the whole point of this endpoint (product create flow
        # never has to know a branch_id exists at all).
        record = InventoryRecord(
            variant_id=variant_id,
            branch_id=default_branch.id,
            quantity_available=0,
            quantity_reserved=0,
        )
        db.add(record)
        db.flush()

    previous_quantity = record.quantity_available
    delta = update.quantity_available - previous_quantity
    record.quantity_available = update.quantity_available

    if delta != 0:
        db.add(
            InventoryMovement(
                inventory_record_id=record.id,
                # STOCK_IN for the very first stock a variant ever gets
                # (matches create_inventory_record's own "Initial stock"
                # convention in routers/inventory.py), ADJUSTMENT for every
                # later set-to-absolute-value call from the product form.
                movement_type=MovementType.STOCK_IN if previous_quantity == 0 and delta > 0 else MovementType.ADJUSTMENT,
                quantity_changed=delta,
                reason="Product form stock update",
                staff_user_id=current_staff.id,
            )
        )

    db.commit()
    db.refresh(variant)
    return _build_variant_read(variant, db, requesting_staff=current_staff)
