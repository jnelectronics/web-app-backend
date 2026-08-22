# Covers Promotions: public banner listing (respecting the active
# schedule window, not just is_active) and Inventory-Manager-only writes
# for both banners and per-product discounts.

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import uncategorized_group_id, unwrap
from models import (
    Banner,
    Cart,
    CartItem,
    Category,
    DiscountType,
    InventoryMovement,
    InventoryRecord,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductDiscount,
    ProductVariant,
    StaffRole,
    StaffUser,
)
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
    assert response.status_code == 401

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


@pytest.fixture
def promo_cart_setup(db):
    # A real product with real stock and an active 50%-off discount, used
    # to drive the actual cart -> checkout -> payment flow through the real
    # API below - this is what a manual "add to cart, check out, pay"
    # regression test looks like for the 2026-08-22 discount-pricing bug
    # (see CLAUDE.md and schemas.py's CartItemRead comment): the bug never
    # showed up in unit-style tests that build an Order directly, because
    # those already hardcoded the "right" numbers - it only showed up by
    # going through cart.py's and orders.py's own price-resolution code.
    category = Category(
        name=f"Promo Cart Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Promo Cart Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()

    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
    db.add(inventory)

    discount = ProductDiscount(product_id=product.id, discount_type=DiscountType.PERCENTAGE, discount_value=50)
    db.add(discount)
    db.commit()

    yield variant

    # Teardown order matches CLAUDE.md's FK-safe-teardown-order gotcha -
    # cart/order/payment rows the TEST ITSELF creates via the API are
    # cleaned up inside the test function, before this fixture teardown
    # runs (pytest fixtures finalize LIFO).
    db.query(ProductDiscount).filter(ProductDiscount.product_id == product.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == inventory.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_discount_actually_reduces_cart_order_and_payment_totals(client, db, promo_cart_setup):
    variant = promo_cart_setup
    guest_token = f"guest-{uuid.uuid4().hex}"
    headers = {"X-Guest-Token": guest_token}

    # 1. Adding a USh 1,000 item with an active 50%-off discount to the
    # cart must charge (not just display) USh 500.
    cart_response = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(variant.id), "quantity": 1},
        headers=headers,
    )
    assert cart_response.status_code == 200
    cart_data = unwrap(cart_response)
    item = cart_data["items"][0]
    assert item["discounted_price"] == 500.0
    assert item["line_total"] == 500.0
    assert cart_data["subtotal"] == 500.0

    # 2. Checking out must carry that same discounted price onto the
    # order and its line items, not the USh 1,000 list price.
    checkout_response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Promo Tester",
            "guest_phone_number": "+256700000098",
            "delivery_address": "Test Address",
            "district": "Test District",
        },
        headers=headers,
    )
    assert checkout_response.status_code == 200
    order_data = unwrap(checkout_response)
    order_id = order_data["id"]
    assert order_data["subtotal"] == 500.0
    assert order_data["total"] == 500.0
    assert order_data["items"][0]["unit_price"] == 500.0
    assert order_data["items"][0]["line_total"] == 500.0

    # 3. Paying for that order must charge the discounted order total, not
    # whatever amount a client might (still, harmlessly) send - the
    # request below deliberately sends no `amount` field at all, since
    # PaymentInitiate no longer accepts one (see that schema's own
    # comment).
    payment_response = client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"provider": "cash_on_delivery"},
        headers=headers,
    )
    assert payment_response.status_code == 201
    assert unwrap(payment_response)["amount"] == 500.0

    # Teardown - FK-safe order (see CLAUDE.md). Checkout also logs a SOLD
    # InventoryMovement referencing this order - that must go before the
    # Order it references, same reasoning as every other movement-logging
    # test in this project. Checkout marks the cart CONVERTED rather than
    # deleting it, so it's still a real row to clean up here too.
    db.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).delete()
    db.commit()
    db.query(InventoryMovement).filter(InventoryMovement.order_id == uuid.UUID(order_id)).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.order_id == uuid.UUID(order_id)).delete()
    db.commit()
    db.query(Order).filter(Order.id == uuid.UUID(order_id)).delete()
    db.commit()
    cart = db.query(Cart).filter(Cart.guest_token == guest_token).first()
    if cart is not None:
        db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
        db.commit()
        db.query(Cart).filter(Cart.id == cart.id).delete()
        db.commit()
