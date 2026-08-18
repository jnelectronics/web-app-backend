# Covers Promotions: public banner listing (respecting the active
# schedule window, not just is_active) and Inventory-Manager-only writes
# for both banners and per-product discounts.

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import uncategorized_group_id, unwrap
from models import Banner, Category, Product, ProductDiscount, StaffRole, StaffUser
from security import create_access_token, hash_password


@pytest.fixture
def owner_token(db):
    staff = StaffUser(
        full_name="Promo Manager",
        email=f"promomgr-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def scheduled_banners(db):
    now = datetime.now(timezone.utc)
    live = Banner(title="Live Banner", image_url="https://example.com/live.png", is_active=True)
    future = Banner(
        title="Future Banner",
        image_url="https://example.com/future.png",
        is_active=True,
        starts_at=now + timedelta(days=1),
    )
    expired = Banner(
        title="Expired Banner",
        image_url="https://example.com/expired.png",
        is_active=True,
        ends_at=now - timedelta(days=1),
    )
    inactive = Banner(title="Inactive Banner", image_url="https://example.com/off.png", is_active=False)
    db.add_all([live, future, expired, inactive])
    db.commit()

    yield live

    db.query(Banner).filter(Banner.id.in_([live.id, future.id, expired.id, inactive.id])).delete(
        synchronize_session=False
    )
    db.commit()


def test_public_banner_list_only_shows_currently_live_ones(client, scheduled_banners):
    response = client.get("/api/v1/banners")
    assert response.status_code == 200
    titles = {b["title"] for b in unwrap(response)}
    assert titles == {"Live Banner"}


def test_banner_write_requires_owner(client, owner_token, db):
    response = client.post(
        "/api/v1/banners", json={"title": "New Banner", "image_url": "https://example.com/new.png"}
    )
    assert response.status_code in (401, 403)

    response = client.post(
        "/api/v1/banners",
        json={"title": "New Banner", "image_url": "https://example.com/new.png"},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    banner = unwrap(response)
    assert banner["is_active"] is True

    response = client.patch(
        f"/api/v1/banners/{banner['id']}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["is_active"] is False

    # Setting the same value again is a safe no-op
    response = client.patch(
        f"/api/v1/banners/{banner['id']}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["is_active"] is False

    # An inactive/scheduled banner is invisible on the public list...
    response = client.get("/api/v1/banners")
    assert banner["id"] not in {b["id"] for b in unwrap(response)}
    # ...but visible to staff via include_inactive=true
    response = client.get("/api/v1/banners?include_inactive=true", headers=_auth(owner_token))
    assert banner["id"] in {b["id"] for b in unwrap(response)}
    # A non-staff request for include_inactive is rejected, not silently
    # downgraded to the public view
    response = client.get("/api/v1/banners?include_inactive=true")
    assert response.status_code == 401

    db.query(Banner).filter(Banner.id == uuid.UUID(banner["id"])).delete()
    db.commit()


@pytest.fixture
def product(db):
    category = Category(name=f"Promo Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Promo Test Product")
    db.add(product)
    db.commit()

    yield product

    db.query(ProductDiscount).filter(ProductDiscount.product_id == product.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_product_discount_lifecycle(client, product, owner_token):
    response = client.post(
        f"/api/v1/products/{product.id}/discounts",
        json={"discount_type": "percentage", "discount_value": 15},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    discount = unwrap(response)
    assert discount["is_active"] is True

    response = client.put(
        f"/api/v1/products/{product.id}/discounts/{discount['id']}",
        json={"discount_type": "fixed_amount", "discount_value": 5000},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["discount_type"] == "fixed_amount"

    # GET returns the full history for this product, including the one
    # just created above
    response = client.get(f"/api/v1/products/{product.id}/discounts")
    assert response.status_code == 200
    assert discount["id"] in {d["id"] for d in unwrap(response)}

    response = client.patch(
        f"/api/v1/products/{product.id}/discounts/{discount['id']}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["is_active"] is False
