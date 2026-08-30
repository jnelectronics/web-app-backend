# Native "bundle" endpoints for the storefront - added 2026-08-30 per
# Nyson's request. Right now the frontend ships its OWN interim
# BFF (buildHomepageBundle()/buildCatalogueBundle(), a Vercel edge
# function) that fans out to several of our public GET endpoints itself
# (categories, category-groups, store-settings, homepage-sections,
# products) and stitches the results together client-side, cached for 5
# min fresh / 10 min stale-while-revalidate. These two endpoints do that
# same aggregation SERVER-SIDE instead, so the frontend can make ONE
# request instead of five-plus, and so a CDN edge can cache our response
# directly instead of a Vercel edge function re-fetching from us on every
# miss.
#
# Both routes are fully public (no auth at all - matches every one of the
# underlying reads they aggregate) and deliberately NOT wrapped in this
# project's usual {success, message, data} envelope (no
# route_class=EnvelopeRoute) - same reasoning routers/payments.py's
# webhook_router skips it: the caller here is a CDN/browser cache reading
# raw JSON, and Cache-Control needs to be a REAL HTTP response header,
# which is simplest to guarantee by returning a plain JSONResponse rather
# than relying on the envelope machinery to pass extra headers through.

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Category, CategoryGroup, HomepageSection, Product, StoreSettings
from routers.categories import _group_with_categories
from routers.homepage_sections import _resolve_products
from routers.products import _build_product_read
from schemas import CatalogueBundleRead, HomepageBundleRead, HomepageSectionProductsRead

router = APIRouter(prefix="/storefront", tags=["storefront"])

# "public, max-age=60, s-maxage=300" - exactly Nyson's ask: 60s fresh in
# the browser, 5 min fresh at a CDN edge (s-maxage overrides max-age for
# shared caches) before either has to revalidate with us again.
BUNDLE_CACHE_CONTROL = "public, max-age=60, s-maxage=300"

# Mirrors GET /products' own page-size idea when a bundle wants
# "everything" in one page - large enough to cover a real catalogue
# without turning this into a second pagination system of its own.
BUNDLE_PRODUCT_LIMIT = 100

# A homepage section only earns a spot in section_products once it has at
# least this many matching products - the same min-product threshold
# Nyson's own interim BFF already enforces client-side today, moved
# server-side here so the frontend can drop that logic once it switches
# over.
MIN_HOMEPAGE_SECTION_PRODUCTS = 4


def _active_categories(db: Session) -> list[Category]:
    return db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()  # noqa: E712


def _active_category_groups_with_categories(db: Session):
    groups = (
        db.query(CategoryGroup)
        .filter(CategoryGroup.is_active == True)  # noqa: E712
        .order_by(CategoryGroup.display_order)
        .all()
    )
    # Reused straight from routers/categories.py (not duplicated) - same
    # "active-only" branch its own public GET /category-groups takes.
    return [_group_with_categories(db, g, include_inactive_categories=False) for g in groups]


def _store_settings_row(db: Session) -> StoreSettings:
    # Same "always exactly one row" assumption routers/store_settings.py's
    # own _get_settings_row makes - see that function's comment for why a
    # missing row is a deployment problem, not something to recover from.
    settings = db.query(StoreSettings).first()
    if settings is None:
        raise RuntimeError("Store settings are not configured")
    return settings


def _catalogue_products(db: Session) -> list[Product]:
    return (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .order_by(Product.created_at.desc())
        .limit(BUNDLE_PRODUCT_LIMIT)
        .all()
    )


def _section_products(db: Session, sections: list[HomepageSection]) -> list[HomepageSectionProductsRead]:
    entries = []
    for section in sections:
        products = _resolve_products(db, section)
        if len(products) < MIN_HOMEPAGE_SECTION_PRODUCTS:
            continue
        entries.append(
            HomepageSectionProductsRead(
                section_id=section.id,
                products=[_build_product_read(p, db) for p in products],
            )
        )
    return entries


@router.get("/homepage-bundle")
def get_homepage_bundle(db: Session = Depends(get_db)):
    sections = (
        db.query(HomepageSection)
        .filter(HomepageSection.is_enabled == True)  # noqa: E712
        .order_by(HomepageSection.display_order)
        .all()
    )

    bundle = HomepageBundleRead(
        categories=_active_categories(db),
        category_groups=_active_category_groups_with_categories(db),
        store_settings=_store_settings_row(db),
        homepage_sections=sections,
        catalogue_products=[_build_product_read(p, db) for p in _catalogue_products(db)],
        section_products=_section_products(db, sections),
    )
    return JSONResponse(content=jsonable_encoder(bundle), headers={"Cache-Control": BUNDLE_CACHE_CONTROL})


@router.get("/catalogue-bundle")
def get_catalogue_bundle(db: Session = Depends(get_db)):
    bundle = CatalogueBundleRead(
        categories=_active_categories(db),
        products=[_build_product_read(p, db) for p in _catalogue_products(db)],
    )
    return JSONResponse(content=jsonable_encoder(bundle), headers={"Cache-Control": BUNDLE_CACHE_CONTROL})
