# All the HTTP routes for the "products" domain live here.

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from audit import write_audit_log
from cloudinary_client import CloudinaryError, delete_image, is_configured, upload_image
from database import get_db
from envelope import EnvelopeRoute
from models import Category, Product, ProductDiscount, ProductImage, StaffRole, StaffUser
from pagination import build_pagination_meta
from schemas import PaginatedResponse, ProductCreate, ProductImageRead, ProductRead
from security import require_staff_role

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


def _build_product_read(product: Product, db: Session) -> ProductRead:
    images = (
        db.query(ProductImage)
        .filter(ProductImage.product_id == product.id)
        .order_by(ProductImage.display_order)
        .all()
    )
    return ProductRead(
        id=product.id,
        category_id=product.category_id,
        name=product.name,
        description=product.description,
        is_featured=product.is_featured,
        slug=product.slug,
        is_discounted=_is_product_discounted(db, product.id),
        images=[ProductImageRead.model_validate(i) for i in images],
        is_active=product.is_active,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


class ImageUploadUnavailableError(Exception):
    # Raised when Cloudinary isn't configured yet (see
    # cloudinary_client.is_configured) - same idea as routers/payments.py's
    # PaymentsUnavailableError: turned into a clean 503 by the handler
    # registered in main.py, instead of every upload attempt failing deep
    # inside a Cloudinary auth error.
    pass

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
def read_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return _build_product_read(product, db)


@router.get("", response_model=PaginatedResponse[ProductRead])
def list_products(
    skip: int = 0,
    limit: int = 10,
    category: uuid.UUID | None = None,
    search: str | None = None,
    featured: bool | None = None,
    discounted: bool | None = None,
    # "-created_at" (a leading dash = descending) mirrors the frontend's
    # own requested syntax. Only Product's OWN columns are sortable here -
    # price lives on ProductVariant, not Product (a product can have
    # several variants at different prices), so a price sort on this
    # endpoint isn't something a single column can answer; not attempted.
    sort: str | None = None,
    db: Session = Depends(get_db),
):
    # .filter(Product.is_active == True) excludes soft-deleted products from
    # the normal browsing list - they still exist in the database, just hidden
    # from this query. This is why we can't simply .query(Product) anymore.
    query = db.query(Product).filter(Product.is_active == True)  # noqa: E712

    if category is not None:
        query = query.filter(Product.category_id == category)
    if search:
        # Matches name OR description, per the request - ILIKE is
        # Postgres's case-insensitive LIKE, so "phone" matches "iPhone".
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(Product.name.ilike(like_pattern), Product.description.ilike(like_pattern))
        )
    if featured is not None:
        query = query.filter(Product.is_featured == featured)
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
        items=[_build_product_read(p, db) for p in products],
        pagination=build_pagination_meta(skip, limit, total),
    )


@router.post("", response_model=ProductRead)
def create_product(
    product: ProductCreate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
        # Generated once, here, and never touched again - see the model's
        # comment on why (existing links/SEO shouldn't break if the name
        # is edited later).
        slug=_generate_unique_slug(db, product.name),
    )
    db.add(new_product)       # stage it for saving
    db.flush()                # assigns new_product.id for the audit log entry below
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="product.create",
        resource_type="product",
        resource_id=new_product.id,
        new_value={"name": new_product.name, "category_id": str(new_product.category_id)},
    )
    db.commit()               # actually write it to Postgres
    db.refresh(new_product)   # pull back the id/timestamps Postgres just assigned
    return _build_product_read(new_product, db)


@router.put("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    product: ProductCreate,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
    return _build_product_read(existing, db)


# DELETE no longer removes the row - per the docs, products are
# soft-deleted (deactivated) so nothing is ever actually lost.
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: uuid.UUID,
    current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
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
