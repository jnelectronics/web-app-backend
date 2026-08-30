# Covers the native storefront bundle endpoints (2026-08-30, per Nyson's
# request) - GET /storefront/homepage-bundle and GET
# /storefront/catalogue-bundle replace the frontend's own interim BFF fan-
# out with one server-side aggregated response each. Both are fully
# public and NOT envelope-wrapped (see routers/storefront.py's own
# comment for why), so these tests read response.json() directly instead
# of using conftest.unwrap().

import uuid

import pytest

from conftest import uncategorized_group_id
from models import Category, HomepageSection, HomepageSectionType, Product


@pytest.fixture
def bundle_setup(db):
    category_a = Category(
        name=f"Bundle Qualifying Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    category_b = Category(
        name=f"Bundle Short Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    db.add_all([category_a, category_b])
    db.flush()

    # category_a gets 4 products (meets MIN_HOMEPAGE_SECTION_PRODUCTS),
    # category_b gets 2 (falls short) - lets one test assert the threshold
    # actually excludes a section from section_products.
    products_a = [Product(category_id=category_a.id, name=f"Bundle Product A{i}") for i in range(4)]
    products_b = [Product(category_id=category_b.id, name=f"Bundle Product B{i}") for i in range(2)]
    db.add_all(products_a + products_b)
    db.flush()

    section_a = HomepageSection(
        title="Qualifying Section",
        section_type=HomepageSectionType.BY_CATEGORY,
        category_id=category_a.id,
        display_order=0,
        is_enabled=True,
    )
    section_b = HomepageSection(
        title="Short Section",
        section_type=HomepageSectionType.BY_CATEGORY,
        category_id=category_b.id,
        display_order=1,
        is_enabled=True,
    )
    section_disabled = HomepageSection(
        title="Disabled Section",
        section_type=HomepageSectionType.BY_CATEGORY,
        category_id=category_a.id,
        display_order=2,
        is_enabled=False,
    )
    db.add_all([section_a, section_b, section_disabled])
    db.commit()

    yield {
        "category_a": category_a,
        "category_b": category_b,
        "products_a": products_a,
        "products_b": products_b,
        "section_a": section_a,
        "section_b": section_b,
        "section_disabled": section_disabled,
    }

    db.query(HomepageSection).filter(
        HomepageSection.id.in_([section_a.id, section_b.id, section_disabled.id])
    ).delete(synchronize_session=False)
    db.commit()
    db.query(Product).filter(
        Product.id.in_([p.id for p in products_a + products_b])
    ).delete(synchronize_session=False)
    db.commit()
    db.query(Category).filter(Category.id.in_([category_a.id, category_b.id])).delete(
        synchronize_session=False
    )
    db.commit()


def test_catalogue_bundle_returns_active_categories_and_products(client, bundle_setup):
    response = client.get("/api/v1/storefront/catalogue-bundle")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60, s-maxage=300"

    data = response.json()
    category_ids = {c["id"] for c in data["categories"]}
    product_ids = {p["id"] for p in data["products"]}

    assert str(bundle_setup["category_a"].id) in category_ids
    assert str(bundle_setup["category_b"].id) in category_ids
    for product in bundle_setup["products_a"] + bundle_setup["products_b"]:
        assert str(product.id) in product_ids


def test_homepage_bundle_applies_min_product_threshold_to_section_products(client, bundle_setup):
    response = client.get("/api/v1/storefront/homepage-bundle")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60, s-maxage=300"

    data = response.json()

    # homepage_sections is metadata only, enabled sections only - the
    # short section still appears here, only section_products excludes it.
    section_ids = {s["id"] for s in data["homepage_sections"]}
    assert str(bundle_setup["section_a"].id) in section_ids
    assert str(bundle_setup["section_b"].id) in section_ids
    assert str(bundle_setup["section_disabled"].id) not in section_ids

    section_products_by_id = {entry["section_id"]: entry["products"] for entry in data["section_products"]}
    assert str(bundle_setup["section_a"].id) in section_products_by_id
    assert len(section_products_by_id[str(bundle_setup["section_a"].id)]) == 4
    # Below the 4-product threshold - excluded entirely, not just empty.
    assert str(bundle_setup["section_b"].id) not in section_products_by_id

    category_ids = {c["id"] for c in data["categories"]}
    assert str(bundle_setup["category_a"].id) in category_ids

    assert "catalogue_filter_mode" in data["store_settings"]

    catalogue_product_ids = {p["id"] for p in data["catalogue_products"]}
    for product in bundle_setup["products_a"]:
        assert str(product.id) in catalogue_product_ids
