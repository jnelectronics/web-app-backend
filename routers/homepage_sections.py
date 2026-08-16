# All the HTTP routes for the "homepage sections" domain live here - a
# configurable row of products on the homepage (e.g. "Featured products"),
# each backed by a PRESET rule (models.HomepageSectionType) rather than
# free-form manual product curation - see models.HomepageSection's own
# comment. Two routers, same public/admin split as routers/store_settings.py.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Category, HomepageSection, HomepageSectionType, Product, ProductHomepageSection, StaffRole, StaffUser
from schemas import HomepageSectionCreate, HomepageSectionReorder, HomepageSectionRead, HomepageSectionWithProducts
from security import require_staff_role

public_router = APIRouter(prefix="/homepage-sections", tags=["homepage-sections"], route_class=EnvelopeRoute)
admin_router = APIRouter(prefix="/admin/homepage-sections", tags=["homepage-sections"], route_class=EnvelopeRoute)


def _resolve_products(db: Session, section: HomepageSection) -> list[Product]:
    # Local import to avoid a circular import (routers/products.py never
    # imports this file) - same reasoning as routers/promotions.py's
    # apply_promotion_to_product.
    from routers.products import _on_sale_product_ids

    query = db.query(Product).filter(Product.is_active == True)  # noqa: E712

    if section.section_type == HomepageSectionType.FEATURED:
        query = query.filter(Product.is_featured == True)  # noqa: E712
    elif section.section_type == HomepageSectionType.NEW_ARRIVAL:
        query = query.filter(Product.is_new_arrival == True)  # noqa: E712
    elif section.section_type == HomepageSectionType.ON_SALE:
        # Same "flag alone isn't enough" rule as routers/products.py's
        # on_sale=true list filter - reuses the exact same query so the
        # rule can't drift between the two call sites.
        query = query.filter(Product.id.in_(_on_sale_product_ids(db)))
    elif section.section_type == HomepageSectionType.BY_CATEGORY:
        if section.category_id is None:
            return []
        query = query.filter(Product.category_id == section.category_id)
    elif section.section_type == HomepageSectionType.CURATED:
        # Membership comes from the join table, not any flag/category on
        # Product itself - a subquery of product_ids linked to THIS
        # section, same shape as ON_SALE's _on_sale_product_ids above.
        member_ids = (
            db.query(ProductHomepageSection.product_id)
            .filter(ProductHomepageSection.homepage_section_id == section.id)
        )
        query = query.filter(Product.id.in_(member_ids))
    # ALL_PRODUCTS: no extra filter - every active product qualifies.

    query = query.order_by(Product.created_at.desc())
    # NULL max_products = uncapped here - the frontend's own default (12)
    # applies when it's null, per Nyson's 2026-08-13 doc; this endpoint
    # just returns whatever was actually asked for.
    if section.max_products is not None:
        query = query.limit(section.max_products)
    return query.all()


def _build_section_with_products(db: Session, section: HomepageSection, *, resolve: bool) -> HomepageSectionWithProducts:
    from routers.products import _build_product_read

    products = [_build_product_read(p, db) for p in _resolve_products(db, section)] if resolve else []
    return HomepageSectionWithProducts(
        id=section.id,
        title=section.title,
        description=section.description,
        section_type=section.section_type,
        category_id=section.category_id,
        slug=section.slug,
        display_order=section.display_order,
        is_enabled=section.is_enabled,
        max_products=section.max_products,
        view_all_href=section.view_all_href,
        created_at=section.created_at,
        updated_at=section.updated_at,
        products=products,
    )


def _validate_category_for_section_type(db: Session, payload: HomepageSectionCreate) -> uuid.UUID | None:
    if payload.section_type != HomepageSectionType.BY_CATEGORY:
        # category_id is only meaningful for by_category - silently
        # dropped for every other type rather than left to linger as a
        # stale reference on a section that no longer uses it.
        return None
    if payload.category_id is None:
        raise HTTPException(status_code=422, detail="category_id is required when section_type is by_category")
    category = db.get(Category, payload.category_id)
    # Treat an inactive category the same as a missing one - a homepage
    # section pointing at a soft-deleted category would otherwise silently
    # resolve to an empty product list forever, with no obvious signal to
    # the admin who configured it.
    if category is None or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found")
    return payload.category_id


def _validate_slug_for_section_type(
    db: Session, payload: HomepageSectionCreate, *, exclude_section_id: uuid.UUID | None = None
) -> str | None:
    if payload.section_type != HomepageSectionType.CURATED:
        # slug is only meaningful for curated - same "silently dropped for
        # every other type" reasoning as category_id above.
        return None
    if not payload.slug:
        raise HTTPException(status_code=422, detail="slug is required when section_type is curated")

    query = db.query(HomepageSection).filter(HomepageSection.slug == payload.slug)
    if exclude_section_id is not None:
        # On an update, a section is always allowed to keep its OWN slug -
        # only a DIFFERENT section already holding it is a real conflict.
        query = query.filter(HomepageSection.id != exclude_section_id)
    if query.first() is not None:
        raise HTTPException(status_code=422, detail=f"slug '{payload.slug}' is already in use by another homepage section")
    return payload.slug


# Fixed-path route registered BEFORE "/{section_id}" below - same
# route-registration-order reasoning as routers/inventory.py's
# "/movements" and routers/orders.py's "/staff" (see CLAUDE.md).
@admin_router.patch("/reorder", response_model=list[HomepageSectionRead])
def reorder_homepage_sections(
    reorder: HomepageSectionReorder,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Caller must state the full end result (front to back), same "not a
    # series of individual moves" reasoning as schemas.HomepageSectionReorder's
    # own comment - assigns display_order by position in the list. The
    # submitted set of ids must exactly match every section that currently
    # exists: a stale/unknown id, a duplicate, or a partial list (missing
    # some existing section) is rejected outright rather than partially
    # applied - a partial apply would leave whichever sections were left out
    # holding their old display_order, which can collide with the newly
    # assigned ranks and break the "contiguous ranks" guarantee silently.
    existing_sections = db.query(HomepageSection).all()
    existing_ids = {section.id for section in existing_sections}
    submitted_ids = reorder.ordered_ids
    if len(submitted_ids) != len(set(submitted_ids)) or set(submitted_ids) != existing_ids:
        raise HTTPException(
            status_code=422,
            detail="ordered_ids must contain every existing homepage section id exactly once, with no unknown ids.",
        )

    sections_by_id = {section.id: section for section in existing_sections}
    for index, section_id in enumerate(submitted_ids):
        sections_by_id[section_id].display_order = index
    db.commit()
    return db.query(HomepageSection).order_by(HomepageSection.display_order).all()


@public_router.get("", response_model=list[HomepageSectionWithProducts])
def list_public_homepage_sections(
    include_products: bool = True,
    db: Session = Depends(get_db),
):
    sections = (
        db.query(HomepageSection)
        .filter(HomepageSection.is_enabled == True)  # noqa: E712
        .order_by(HomepageSection.display_order)
        .all()
    )
    return [_build_section_with_products(db, s, resolve=include_products) for s in sections]


@admin_router.get("", response_model=list[HomepageSectionWithProducts])
def list_admin_homepage_sections(
    include_products: bool = False,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    sections = db.query(HomepageSection).order_by(HomepageSection.display_order).all()
    return [_build_section_with_products(db, s, resolve=include_products) for s in sections]


@admin_router.post("", response_model=HomepageSectionRead)
def create_homepage_section(
    section: HomepageSectionCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    category_id = _validate_category_for_section_type(db, section)
    slug = _validate_slug_for_section_type(db, section)
    new_section = HomepageSection(
        title=section.title,
        description=section.description,
        section_type=section.section_type,
        category_id=category_id,
        slug=slug,
        display_order=section.display_order,
        is_enabled=section.is_enabled,
        max_products=section.max_products,
        view_all_href=section.view_all_href,
    )
    db.add(new_section)
    db.commit()
    db.refresh(new_section)
    return new_section


@admin_router.put("/{section_id}", response_model=HomepageSectionRead)
def update_homepage_section(
    section_id: uuid.UUID,
    section: HomepageSectionCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    existing = db.get(HomepageSection, section_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Homepage section not found")

    category_id = _validate_category_for_section_type(db, section)
    slug = _validate_slug_for_section_type(db, section, exclude_section_id=section_id)
    existing.title = section.title
    existing.description = section.description
    existing.section_type = section.section_type
    existing.category_id = category_id
    existing.slug = slug
    existing.display_order = section.display_order
    existing.is_enabled = section.is_enabled
    existing.max_products = section.max_products
    existing.view_all_href = section.view_all_href
    db.commit()
    db.refresh(existing)
    return existing


@admin_router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_homepage_section(
    section_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # A REAL delete, not soft - unlike products/categories, a homepage
    # section is pure display configuration with nothing else referencing
    # it, and is_enabled already covers the "hide without deleting" case.
    existing = db.get(HomepageSection, section_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Homepage section not found")

    # This project doesn't use SQLAlchemy relationship()/ORM cascade
    # anywhere (see CLAUDE.md) - the FK from product_homepage_sections has
    # no ON DELETE CASCADE either, so any membership rows have to be
    # cleared explicitly first, in the same transaction, or the delete
    # below would fail with a foreign key violation the instant a curated
    # section with at least one product actually has members.
    db.query(ProductHomepageSection).filter(ProductHomepageSection.homepage_section_id == section_id).delete()
    db.delete(existing)
    db.commit()
