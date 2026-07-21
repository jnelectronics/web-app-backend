# All the HTTP routes for the "products" domain live here.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from models import Category, Product, ProductImage, StaffRole, StaffUser
from schemas import ProductCreate, ProductImageCreate, ProductImageRead, ProductRead
from security import require_staff_role

MAX_IMAGES_PER_PRODUCT = 5

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
    return product


@router.get("", response_model=list[ProductRead])
def list_products(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    # .filter(Product.is_active == True) excludes soft-deleted products from
    # the normal browsing list - they still exist in the database, just hidden
    # from this query. This is why we can't simply .query(Product) anymore.
    return (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .offset(skip)
        .limit(limit)
        .all()
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

    new_product = Product(category_id=product.category_id, name=product.name)
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
    return new_product


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
    return existing


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
    image: ProductImageCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    existing_count = db.query(ProductImage).filter(ProductImage.product_id == product_id).count()
    if existing_count >= MAX_IMAGES_PER_PRODUCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_IMAGES_PER_PRODUCT} images",
        )

    new_image = ProductImage(
        product_id=product_id,
        image_url=image.image_url,
        display_order=image.display_order,
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
    image: ProductImageCreate,
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

    # Doesn't touch is_primary - swapping the picture shouldn't silently
    # change which one is the primary display image.
    existing.image_url = image.image_url
    existing.display_order = image.display_order
    db.commit()
    db.refresh(existing)
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

    db.delete(existing)
    db.commit()


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
