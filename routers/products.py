# All the HTTP routes for the "products" domain live here.

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import write_audit_log
from cloudinary_client import CloudinaryError, delete_image, is_configured, upload_image
from database import get_db
from envelope import EnvelopeRoute
from models import (
    Category,
    HomepageSection,
    HomepageSectionType,
    InventoryMovement,
    InventoryRecord,
    MovementType,
    Product,
    ProductDiscount,
    ProductHomepageSection,
    ProductImage,
    ProductVariant,
    Promotion,
    StaffRole,
    StaffUser,
)
from pagination import build_pagination_meta
from schemas import PaginatedResponse, ProductCreate, ProductHomepageSectionsSync, ProductImageRead, ProductRead, ProductUpdate
from security import decode_token_claims, require_staff_role

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_PRODUCT = 5


def _slugify(name: str) -> str:
    # Same logic as the migration's copy (alembic/versions/6e336ac5877a_...)
    # - that one is a standalone copy on purpose (migrations shouldn't
    # depend on application code that might change later), this is the
    # version actually used for every NEW product going forward.
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "product"


def _generate_unique_slug(db: Session, name: str) -> str:
    # Only ever called once, at product creation - slugs never change
    # afterward even if the name is edited later (see the model's comment),
    # so existing links/SEO never break out from under a customer.
    base_slug = _slugify(name)
    slug = base_slug
    suffix = 2
    while db.query(Product).filter(Product.slug == slug).first() is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _is_product_discounted(db: Session, product_id: uuid.UUID) -> bool:
    # "Currently discounted" can only be answered by checking the CURRENT
    # time against product_discounts' own window - see the model's comment
    # on why this isn't a stored column. NULL starts_at/ends_at means "no
    # limit on that side" (a discount active from the start, or with no
    # planned end).
    #
    # starts_at/ends_at are plain TIMESTAMP columns (no timezone) - the
    # same naive-vs-aware gotcha CLAUDE.md documents for RefreshToken.
    # datetime.now(timezone.utc) then .replace(tzinfo=None) gives a NAIVE
    # value that's still really UTC underneath, matching what these
    # columns actually store, instead of letting an aware value get
    # silently reinterpreted through Postgres's session timezone.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (
        db.query(ProductDiscount)
        .filter(
            ProductDiscount.product_id == product_id,
            ProductDiscount.is_active == True,  # noqa: E712
            or_(ProductDiscount.starts_at.is_(None), ProductDiscount.starts_at <= now),
            or_(ProductDiscount.ends_at.is_(None), ProductDiscount.ends_at >= now),
        )
        .first()
        is not None
    )


def _on_sale_product_ids(db: Session):
    # is_on_sale=true on the PRODUCT row only reflects what was true the
    # last time someone wrote to it - a promotion's own starts_at/ends_at
    # window can lapse later with no follow-up write ever flipping the
    # flag back off (nothing re-checks it on a schedule). So "really on
    # sale right now" - for filtering/section resolution, as opposed to
    # just displaying the flag on a single product - has to mean the flag
    # AND a currently-active-and-in-window Promotion, same "can't trust a
    # stored boolean alone" reasoning as _is_product_discounted above.
    # Shared with routers/homepage_sections.py's ON_SALE section
    # resolution, so this one rule can't drift between the two call sites.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (
        db.query(Product.id)
        .join(Promotion, Product.applied_promotion_id == Promotion.id)
        .filter(
            Product.is_on_sale == True,  # noqa: E712
            Promotion.is_active == True,  # noqa: E712
            or_(Promotion.starts_at.is_(None), Promotion.starts_at <= now),
            or_(Promotion.ends_at.is_(None), Promotion.ends_at >= now),
        )
        .scalar_subquery()
    )


def _homepage_section_ids_for_product(db: Session, product_id: uuid.UUID) -> list[uuid.UUID]:
    # Which CURATED homepage sections this product currently belongs to -
    # see schemas.ProductRead.homepage_section_ids' own comment for why
    # this is assembled here rather than being an ORM attribute.
    rows = (
        db.query(ProductHomepageSection.homepage_section_id)
        .filter(ProductHomepageSection.product_id == product_id)
        .all()
    )
    return [row[0] for row in rows]


def _default_variant(product_id: uuid.UUID, db: Session) -> ProductVariant | None:
    # "Default" = the oldest ACTIVE variant under this product - for the
    # vast majority of products (created via the 2026-08-20 collapsed-create
    # flow) this is the only variant there is. A product that's since grown
    # a second, separately-stocked variant still just surfaces its first
    # one here; the full set is still only ever available via
    # GET /variants?product_id=<id>, unchanged.
    return (
        db.query(ProductVariant)
        .filter(ProductVariant.product_id == product_id, ProductVariant.is_active == True)  # noqa: E712
        .order_by(ProductVariant.created_at.asc())
        .first()
    )


def _build_product_read(product: Product, db: Session, *, requesting_staff: StaffUser | None = None) -> ProductRead:
    images = (
        db.query(ProductImage)
        .filter(ProductImage.product_id == product.id)
        .order_by(ProductImage.display_order)
        .all()
    )

    default_variant = _default_variant(product.id, db)

    # quantity_available is a REAL number, so - same reasoning as
    # VariantRead.quantity_available in schemas.py - only ever computed for
    # a staff-authenticated caller; stays None for the public storefront.
    quantity_available = None
    if requesting_staff is not None and default_variant is not None:
        record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == default_variant.id).first()
        quantity_available = record.quantity_available if record is not None else 0

    return ProductRead(
        id=product.id,
        category_id=product.category_id,
        name=product.name,
        description=product.description,
        is_featured=product.is_featured,
        is_new_arrival=product.is_new_arrival,
        is_on_sale=product.is_on_sale,
        applied_promotion_id=product.applied_promotion_id,
        slug=product.slug,
        is_discounted=_is_product_discounted(db, product.id),
        images=[ProductImageRead.model_validate(i) for i in images],
        is_active=product.is_active,
        created_at=product.created_at,
        updated_at=product.updated_at,
        homepage_section_ids=_homepage_section_ids_for_product(db, product.id),
        sku=default_variant.sku if default_variant is not None else None,
        price=default_variant.price if default_variant is not None else None,
        quantity_available=quantity_available,
    )


class ImageUploadUnavailableError(Exception):
    # Raised when Cloudinary isn't configured yet (see
    # cloudinary_client.is_configured) - same idea as routers/payments.py's
    # PaymentsUnavailableError: turned into a clean 503 by the handler
    # registered in main.py, instead of every upload attempt failing deep
    # inside a Cloudinary auth error.
    pass


class OnSaleRequiresPromotionError(Exception):
    # BR (frontend-requested, §5): is_on_sale=true is only allowed while a
    # real, currently-active library Promotion is applied - see
    # _assert_on_sale_has_promotion below. A plain Exception (not raised as
    # an inline HTTPException) so main.py's @app.exception_handler can turn
    # it into one consistent 422 response, same pattern as every other
    # domain-rule exception in this project.
    pass


def _assert_on_sale_has_promotion(db: Session, product: Product) -> None:
    if not product.is_on_sale:
        return

    if product.applied_promotion_id is None:
        raise OnSaleRequiresPromotionError(
            "is_on_sale requires a promotion to be applied first - "
            "see POST /products/{id}/apply-promotion"
        )

    promotion = db.get(Promotion, product.applied_promotion_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    currently_active = (
        promotion is not None
        and promotion.is_active
        and (promotion.starts_at is None or promotion.starts_at <= now)
        and (promotion.ends_at is None or promotion.ends_at >= now)
    )
    if not currently_active:
        raise OnSaleRequiresPromotionError(
            "The applied promotion is not currently active - apply a different one before enabling On Sale"
        )


# Same "optional auth" trick as routers/variants.py's GET routes - the
# public storefront calls list_products/read_product with no token at all,
# but the admin catalogue view needs to see archived (is_active=false)
# products too, which is staff-only information.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


def _current_staff_or_none(credentials: HTTPAuthorizationCredentials | None, db: Session) -> StaffUser | None:
    if credentials is None:
        return None
    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") != "staff":
        return None
    staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
    if staff is None or not staff.is_active:
        return None
    return staff

# APIRouter is like a mini FastAPI app - we define routes on it here, then
# "plug it into" the real app in main.py with app.include_router().
# prefix="/products" means every path below only needs to add whatever
# comes AFTER "/products" (e.g. "" means exactly "/products", "/{id}"
# means "/products/{id}"). route_class=EnvelopeRoute wraps every response
# from this router in the standard success envelope - see envelope.py.
router = APIRouter(prefix="/products", tags=["products"], route_class=EnvelopeRoute)


# product_id is now a uuid.UUID (not int) since that's the real primary key
# type - FastAPI validates the URL segment is a real UUID automatically,
# same way it validated integers before.
@router.get("/{product_id}", response_model=ProductRead)
def read_product(
    product_id: uuid.UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return _build_product_read(product, db, requesting_staff=_current_staff_or_none(credentials, db))


@router.get("", response_model=PaginatedResponse[ProductRead])
def list_products(
    skip: int = 0,
    limit: int = 10,
    category: uuid.UUID | None = None,
    # Active products linked to this CURATED homepage section via
    # ProductHomepageSection - the "View all" link on the storefront for a
    # curated section (see routers/homepage_sections.py) lands here.
    homepage_section: uuid.UUID | None = None,
    search: str | None = None,
    featured: bool | None = None,
    discounted: bool | None = None,
    on_sale: bool | None = None,
    new_arrival: bool | None = None,
    # None (the default) = today's existing behavior: active products
    # only. Explicit True/False overrides that - False is the "show me
    # archived products" admin view (§5's ?archived=true is the same
    # request under a different name, so this one query param covers
    # both), and is staff-only since which products got soft-deleted isn't
    # public information.
    active: bool | None = None,
    # "-created_at" (a leading dash = descending) mirrors the frontend's
    # own requested syntax. Only Product's OWN columns are sortable here -
    # price lives on ProductVariant, not Product (a product can have
    # several variants at different prices), so a price sort on this
    # endpoint isn't something a single column can answer; not attempted.
    sort: str | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    requesting_staff = _current_staff_or_none(credentials, db)

    if active is None:
        # .filter(Product.is_active == True) excludes soft-deleted products
        # from the normal browsing list - they still exist in the
        # database, just hidden from this query.
        query = db.query(Product).filter(Product.is_active == True)  # noqa: E712
    else:
        if active is False and requesting_staff is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
        query = db.query(Product).filter(Product.is_active == active)

    if category is not None:
        query = query.filter(Product.category_id == category)
    if homepage_section is not None:
        member_ids = (
            db.query(ProductHomepageSection.product_id)
            .filter(ProductHomepageSection.homepage_section_id == homepage_section)
        )
        query = query.filter(Product.id.in_(member_ids))
    if search:
        # Matches name OR description, per the request - ILIKE is
        # Postgres's case-insensitive LIKE, so "phone" matches "iPhone".
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(Product.name.ilike(like_pattern), Product.description.ilike(like_pattern))
        )
    if featured is not None:
        query = query.filter(Product.is_featured == featured)
    if new_arrival is not None:
        query = query.filter(Product.is_new_arrival == new_arrival)
    if on_sale is not None:
        on_sale_product_ids = _on_sale_product_ids(db)
        if on_sale:
            query = query.filter(Product.id.in_(on_sale_product_ids))
        else:
            query = query.filter(Product.id.notin_(on_sale_product_ids))
    if discounted is not None:
        # Same "currently active" window _is_product_discounted checks per
        # product, expressed here as a set membership test so it can
        # filter the whole query instead of being computed row-by-row
        # after the fact.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        discounted_product_ids = (
            db.query(ProductDiscount.product_id)
            .filter(
                ProductDiscount.is_active == True,  # noqa: E712
                or_(ProductDiscount.starts_at.is_(None), ProductDiscount.starts_at <= now),
                or_(ProductDiscount.ends_at.is_(None), ProductDiscount.ends_at >= now),
            )
            .scalar_subquery()
        )
        if discounted:
            query = query.filter(Product.id.in_(discounted_product_ids))
        else:
            query = query.filter(Product.id.notin_(discounted_product_ids))

    sort_columns = {"name": Product.name, "created_at": Product.created_at}
    if sort:
        descending = sort.startswith("-")
        sort_key = sort[1:] if descending else sort
        sort_column = sort_columns.get(sort_key)
        if sort_column is not None:
            query = query.order_by(sort_column.desc() if descending else sort_column.asc())

    total = query.count()
    products = query.offset(skip).limit(limit).all()
    return PaginatedResponse[ProductRead](
        items=[_build_product_read(p, db, requesting_staff=requesting_staff) for p in products],
        pagination=build_pagination_meta(skip, limit, total),
    )


@router.post("", response_model=ProductRead)
def create_product(
    product: ProductCreate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # Check the referenced category is real BEFORE inserting - otherwise
    # Postgres itself rejects it with a raw database error (500), instead
    # of the clean 404 a client can actually act on.
    if db.get(Category, product.category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")

    new_product = Product(
        category_id=product.category_id,
        name=product.name,
        description=product.description,
        is_featured=product.is_featured,
        is_new_arrival=product.is_new_arrival,
        is_on_sale=product.is_on_sale,
        # Generated once, here, and never touched again - see the model's
        # comment on why (existing links/SEO shouldn't break if the name
        # is edited later).
        slug=_generate_unique_slug(db, product.name),
    )
    # A brand-new product can never already have a promotion applied (that
    # needs a real product id to target), so this only ever passes when
    # is_on_sale is False - see the field's own comment on ProductCreate.
    _assert_on_sale_has_promotion(db, new_product)

    db.add(new_product)       # stage it for saving
    db.flush()                # assigns new_product.id for the variant below

    # Every product created through this endpoint gets exactly ONE
    # purchasable variant up front - collapses what used to be two separate
    # staff actions (create the product, then separately create its
    # variant) into one, since the vast majority of products never need
    # more than one SKU/price (added 2026-08-20, see CLAUDE.md's note).
    # Staff can still add a genuinely separate variant (a different
    # color/size that needs its own stock count) afterward via
    # POST /products/{id}/variants (routers/variants.py's create_variant).
    new_variant = ProductVariant(
        product_id=new_product.id,
        sku=product.sku,
        price=product.price,
    )
    db.add(new_variant)
    try:
        db.flush()  # assigns new_variant.id for the inventory record below
    except IntegrityError:
        # Hits ProductVariant.sku's own unique=True constraint - same
        # reasoning as routers/variants.py's create_variant. Roll back the
        # PRODUCT too, not just the variant - a product with no variant at
        # all isn't a state this endpoint should ever leave behind.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A variant with this SKU already exists")

    new_record = InventoryRecord(variant_id=new_variant.id, quantity_available=product.quantity_available)
    db.add(new_record)
    db.flush()  # assigns new_record.id for the movement log entry below

    # Same "log the starting stock as a movement" convention
    # create_inventory_record/set_variant_stock already follow - skipped
    # when quantity_available is 0 since nothing actually moved yet.
    if product.quantity_available > 0:
        db.add(
            InventoryMovement(
                inventory_record_id=new_record.id,
                movement_type=MovementType.STOCK_IN,
                quantity_changed=product.quantity_available,
                reason="Initial stock",
                staff_user_id=current_staff.id,
            )
        )

    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.create",
        resource_type="product",
        resource_id=new_product.id,
        new_value={
            "name": new_product.name,
            "category_id": str(new_product.category_id),
            "sku": product.sku,
            "price": product.price,
        },
    )
    db.commit()               # actually write it to Postgres
    db.refresh(new_product)   # pull back the id/timestamps Postgres just assigned
    return _build_product_read(new_product, db, requesting_staff=current_staff)


@router.put("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    product: ProductUpdate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Product, product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    if db.get(Category, product.category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")

    previous_value = {"name": existing.name, "category_id": str(existing.category_id)}
    existing.category_id = product.category_id
    existing.name = product.name
    existing.description = product.description
    existing.is_featured = product.is_featured
    existing.is_new_arrival = product.is_new_arrival
    existing.is_on_sale = product.is_on_sale
    # existing.applied_promotion_id is deliberately NOT touched here - it
    # only ever changes via POST/DELETE /products/{id}/apply-promotion
    # (routers/promotions.py), so a plain product-details save can't
    # accidentally clear or fake a promotion link. This check uses
    # whatever applied_promotion_id already sits on the row.
    _assert_on_sale_has_promotion(db, existing)
    # existing.slug is deliberately NOT touched here - see the model's comment.
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.update",
        resource_type="product",
        resource_id=existing.id,
        previous_value=previous_value,
        new_value={"name": existing.name, "category_id": str(existing.category_id)},
    )
    db.commit()
    db.refresh(existing)
    return _build_product_read(existing, db, requesting_staff=current_staff)


# Nyson's 2026-08-13 doc calls this PUT /admin/products/{product_id}/homepage-sections -
# but this project has no separate "/admin/products" prefix anywhere (unlike
# routers/homepage_sections.py's public_router/admin_router split, EVERY
# product route lives under plain "/products" and is staff-gated by a
# Depends(require_staff_role(...)) check instead of a URL namespace - see
# create_product/update_product/delete_product above). Placed here, at
# "/products/{product_id}/homepage-sections", to match how every other
# product write endpoint in this file already works, rather than inventing
# a one-off "/admin" prefix that would exist nowhere else in the API.
@router.put("/{product_id}/homepage-sections", response_model=ProductRead)
def sync_product_homepage_sections(
    product_id: uuid.UUID,
    sync: ProductHomepageSectionsSync,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # De-duplicate up front - the request is "the full end result", same
    # "caller states the end result" shape as HomepageSectionReorder, so a
    # repeated id in the list isn't an error, just redundant.
    requested_ids = set(sync.homepage_section_ids)

    if requested_ids:
        sections = db.query(HomepageSection).filter(HomepageSection.id.in_(requested_ids)).all()
        found_ids = {section.id for section in sections}
        missing_ids = requested_ids - found_ids
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"Homepage section(s) not found: {sorted(str(i) for i in missing_ids)}")

        invalid_sections = [
            str(section.id) for section in sections if section.section_type != HomepageSectionType.CURATED or not section.is_enabled
        ]
        if invalid_sections:
            raise HTTPException(
                status_code=422,
                detail=f"Homepage section(s) are not enabled curated sections: {sorted(invalid_sections)}",
            )

    # Replace all - delete removed, insert new - same atomic "full sync,
    # not incremental add/remove" semantics the doc asks for.
    db.query(ProductHomepageSection).filter(ProductHomepageSection.product_id == product_id).delete()
    for section_id in requested_ids:
        db.add(ProductHomepageSection(product_id=product_id, homepage_section_id=section_id))
    db.commit()
    db.refresh(product)
    return _build_product_read(product, db, requesting_staff=current_staff)


# DELETE no longer removes the row - per the docs, products are
# soft-deleted (deactivated) so nothing is ever actually lost.
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: uuid.UUID,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Product, product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    existing.is_active = False
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.status_change",
        resource_type="product",
        resource_id=existing.id,
        previous_value={"is_active": True},
        new_value={"is_active": False},
    )
    db.commit()


# The other half of DELETE above - archiving a product was always
# reversible in principle (is_active is just a flag), but until now there
# was no route that flipped it back. PATCH (not PUT) because this changes
# exactly one thing, the same reasoning routers/staff.py's set_staff_status
# and every other *StatusUpdate endpoint in this project already follows.
@router.patch("/{product_id}/reactivate", response_model=ProductRead)
def reactivate_product(
    product_id: uuid.UUID,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Product, product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    existing.is_active = True
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.status_change",
        resource_type="product",
        resource_id=existing.id,
        previous_value={"is_active": False},
        new_value={"is_active": True},
    )
    db.commit()
    db.refresh(existing)
    return _build_product_read(existing, db, requesting_staff=current_staff)


@router.post("/{product_id}/images", response_model=ProductImageRead)
def add_product_image(
    product_id: uuid.UUID,
    # File(...) and Form(...) tell FastAPI this route reads a
    # multipart/form-data body (like an HTML <form enctype="multipart/
    # form-data">), not JSON - `file` is the actual uploaded bytes,
    # `display_order` rides alongside it as a plain text form field.
    # UploadFile wraps the upload as a stream, so FastAPI doesn't have to
    # hold the whole file in memory just to receive it.
    file: UploadFile = File(...),
    display_order: int = Form(0),
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    if not is_configured():
        raise ImageUploadUnavailableError("Image uploads will be available soon. Please check back later.")

    # Reject anything that isn't actually an image BEFORE spending an API
    # call on it - content_type comes from the upload's own headers (set by
    # the browser/client), so this is a cheap first check, not a guarantee,
    # but it catches the common accidental-wrong-file case early.
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    existing_count = db.query(ProductImage).filter(ProductImage.product_id == product_id).count()
    if existing_count >= MAX_IMAGES_PER_PRODUCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_IMAGES_PER_PRODUCT} images",
        )

    try:
        # file.file is the underlying raw file object UploadFile wraps -
        # .read() on it is a normal, synchronous read (unlike file.read(),
        # which is async and would need `await`). Every other function in
        # this file is a plain `def`, not `async def`, to stay consistent
        # with the rest of this project's synchronous SQLAlchemy sessions,
        # so this is the version that fits without changing that.
        uploaded = upload_image(file.file.read(), file.filename)
    except CloudinaryError as exc:
        # A genuine infrastructure problem (Cloudinary unreachable,
        # rejected our request, etc.), not an expected business outcome -
        # logger.exception (not .info/.warning) so it becomes a real Sentry
        # Issue, same reasoning as routers/payments.py's PesaPalError handling.
        logger.exception("Cloudinary upload failed for product %s", product_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Image upload failed: {exc}")

    new_image = ProductImage(
        product_id=product_id,
        image_url=uploaded["secure_url"],
        cloudinary_public_id=uploaded["public_id"],
        display_order=display_order,
        # The FIRST image a product gets becomes primary automatically -
        # every other one needs the dedicated /primary endpoint to become it.
        is_primary=(existing_count == 0),
    )
    db.add(new_image)
    db.commit()
    db.refresh(new_image)
    return new_image


@router.put("/{product_id}/images/{image_id}", response_model=ProductImageRead)
def replace_product_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    file: UploadFile = File(...),
    display_order: int = Form(0),
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(ProductImage)
        .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Image not found")

    if not is_configured():
        raise ImageUploadUnavailableError("Image uploads will be available soon. Please check back later.")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    try:
        uploaded = upload_image(file.file.read(), file.filename)
    except CloudinaryError as exc:
        logger.exception("Cloudinary upload failed replacing image %s", image_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Image upload failed: {exc}")

    # Remember the OLD Cloudinary asset before overwriting the row, so it
    # can be cleaned up after the new one is safely saved.
    old_public_id = existing.cloudinary_public_id

    # Doesn't touch is_primary - swapping the picture shouldn't silently
    # change which one is the primary display image.
    existing.image_url = uploaded["secure_url"]
    existing.cloudinary_public_id = uploaded["public_id"]
    existing.display_order = display_order
    db.commit()
    db.refresh(existing)

    if old_public_id:
        try:
            delete_image(old_public_id)
        except CloudinaryError:
            # The replace itself already succeeded and is committed - a
            # failure to clean up the OLD asset shouldn't turn a
            # successful replace into an error response. Still logged as a
            # real Sentry Issue so a pileup of orphaned Cloudinary assets
            # doesn't go unnoticed.
            logger.exception("Failed to delete old Cloudinary asset %s", old_public_id)

    return existing


@router.delete("/{product_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # A real delete (not soft) - unlike products/categories/variants, an
    # image row has no meaning once removed; there's nothing to preserve.
    existing = (
        db.query(ProductImage)
        .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Image not found")

    public_id = existing.cloudinary_public_id
    db.delete(existing)
    db.commit()

    if public_id:
        try:
            delete_image(public_id)
        except CloudinaryError:
            # Same reasoning as replace_product_image above - the DB row is
            # already gone and that's the part the client actually asked
            # for; a leftover Cloudinary asset is a cleanup problem to
            # notice via Sentry, not a reason to fail this request.
            logger.exception("Failed to delete Cloudinary asset %s", public_id)


@router.patch("/{product_id}/images/{image_id}/primary", response_model=ProductImageRead)
def set_primary_product_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    target = (
        db.query(ProductImage)
        .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Image not found")

    # Clear whichever image currently holds primary FIRST, and flush before
    # setting the new one - the partial unique index (models.py) checks
    # "at most one primary per product" on every write, so both can't be
    # true at the same instant even within this one transaction.
    current_primary = (
        db.query(ProductImage)
        .filter(ProductImage.product_id == product_id, ProductImage.is_primary == True)  # noqa: E712
        .first()
    )
    if current_primary is not None and current_primary.id != target.id:
        current_primary.is_primary = False
        db.flush()

    target.is_primary = True
    db.commit()
    db.refresh(target)
    return target
