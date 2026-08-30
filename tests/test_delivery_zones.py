# Covers Kampala door-to-door delivery zones (added 2026-08-30, client UAT
# request) - the admin CRUD (Owner/System-Administrator only, deliberately
# narrower than most of this project's other 2026-08-18-widened modules)
# and the public storefront listing.

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import (
    Cart,
    CartItem,
    Category,
    DeliveryZone,
    InventoryMovement,
    InventoryRecord,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_token(db):
    staff = StaffUser(
        full_name="Zone Test Owner",
        email=f"zoneowner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.delete(staff)
    db.commit()


@pytest.fixture
def sales_attendant_token(db):
    staff = StaffUser(
        full_name="Zone Test Attendant",
        email=f"zoneattendant-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.delete(staff)
    db.commit()


def _cleanup_zone(db, zone_id):
    db.query(DeliveryZone).filter(DeliveryZone.id == uuid.UUID(zone_id)).delete()
    db.commit()


def test_sales_attendant_rejected_from_admin_endpoints(client, db, sales_attendant_token):
    # Nyson's spec: "Admin access: Owner and System Administrator only" -
    # unlike most modules, Sales Attendant is NOT included here at all.
    response = client.post(
        "/api/v1/admin/delivery-zones",
        json={"name": f"Rejected Zone {uuid.uuid4().hex[:8]}", "fee": 5000},
        headers=_auth(sales_attendant_token),
    )
    assert response.status_code == 403

    response = client.get("/api/v1/admin/delivery-zones", headers=_auth(sales_attendant_token))
    assert response.status_code == 403


def test_admin_crud_and_status_toggle(client, db, owner_token):
    name = f"Kampala Central {uuid.uuid4().hex[:8]}"
    create_response = client.post(
        "/api/v1/admin/delivery-zones", json={"name": name, "fee": 5000}, headers=_auth(owner_token)
    )
    assert create_response.status_code == 201
    zone = unwrap(create_response)
    assert zone["fee"] == 5000
    assert zone["is_active"] is True
    zone_id = zone["id"]

    # Shows up on the public list while active.
    public_list = unwrap(client.get("/api/v1/delivery-zones"))
    assert any(z["id"] == zone_id for z in public_list)

    update_response = client.patch(
        f"/api/v1/admin/delivery-zones/{zone_id}", json={"fee": 7500}, headers=_auth(owner_token)
    )
    assert update_response.status_code == 200
    assert unwrap(update_response)["fee"] == 7500
    assert unwrap(update_response)["name"] == name  # untouched, wasn't sent

    status_response = client.patch(
        f"/api/v1/admin/delivery-zones/{zone_id}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert status_response.status_code == 200
    assert unwrap(status_response)["is_active"] is False

    # No longer on the public list once deactivated, but still visible to
    # admin (so it can be reactivated later).
    public_list_after = unwrap(client.get("/api/v1/delivery-zones"))
    assert not any(z["id"] == zone_id for z in public_list_after)

    admin_list = unwrap(client.get("/api/v1/admin/delivery-zones", headers=_auth(owner_token)))
    assert any(z["id"] == zone_id for z in admin_list)

    _cleanup_zone(db, zone_id)


def test_duplicate_name_rejected_case_insensitively(client, db, owner_token):
    name = f"Wakiso {uuid.uuid4().hex[:8]}"
    first = client.post(
        "/api/v1/admin/delivery-zones", json={"name": name, "fee": 3000}, headers=_auth(owner_token)
    )
    assert first.status_code == 201
    zone_id = unwrap(first)["id"]

    dupe = client.post(
        "/api/v1/admin/delivery-zones",
        json={"name": f"  {name.upper()}  ", "fee": 4000},
        headers=_auth(owner_token),
    )
    assert dupe.status_code == 409

    _cleanup_zone(db, zone_id)


def test_public_list_only_returns_active_zones_sorted(client, db, owner_token):
    zone_a = DeliveryZone(name=f"Zone Sort A {uuid.uuid4().hex[:8]}", fee=1000, sort_order=2)
    zone_b = DeliveryZone(name=f"Zone Sort B {uuid.uuid4().hex[:8]}", fee=2000, sort_order=1)
    zone_inactive = DeliveryZone(
        name=f"Zone Sort Inactive {uuid.uuid4().hex[:8]}", fee=3000, sort_order=0, is_active=False
    )
    db.add_all([zone_a, zone_b, zone_inactive])
    db.commit()

    public_list = unwrap(client.get("/api/v1/delivery-zones"))
    ids_in_order = [z["id"] for z in public_list]
    assert str(zone_inactive.id) not in ids_in_order
    assert ids_in_order.index(str(zone_b.id)) < ids_in_order.index(str(zone_a.id))

    db.query(DeliveryZone).filter(
        DeliveryZone.id.in_([zone_a.id, zone_b.id, zone_inactive.id])
    ).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def checkout_setup(db):
    category = Category(
        name=f"Zone Checkout Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Zone Checkout Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()
    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
    db.add(inventory)
    zone = DeliveryZone(name=f"Checkout Zone {uuid.uuid4().hex[:8]}", fee=2000)
    db.add(zone)
    db.commit()

    yield {"variant": variant, "zone": zone}

    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == inventory.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == inventory.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()
    db.query(DeliveryZone).filter(DeliveryZone.id == zone.id).delete()
    db.commit()


def _checkout(client, variant_id, guest_token, **extra):
    cart_response = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(variant_id), "quantity": 1},
        headers={"X-Guest-Token": guest_token},
    )
    assert cart_response.status_code == 200

    return client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Zone Buyer",
            "guest_phone_number": "+256700000097",
            "delivery_address": "123 Test Street",
            "district": "whatever the frontend last had cached",
            **extra,
        },
        headers={"X-Guest-Token": guest_token},
    )


def _cleanup_order(db, order_id, guest_token=None):
    order_uuid = uuid.UUID(order_id)
    db.query(InventoryMovement).filter(InventoryMovement.order_id == order_uuid).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.order_id == order_uuid).delete()
    db.commit()
    db.query(Order).filter(Order.id == order_uuid).delete()
    db.commit()

    if guest_token is not None:
        # Checkout marks the cart CONVERTED rather than deleting it (see
        # CLAUDE.md's FK-safe-teardown-order gotcha) - clean it up too.
        cart = db.query(Cart).filter(Cart.guest_token == guest_token).first()
        if cart is not None:
            db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
            db.commit()
            db.query(Cart).filter(Cart.id == cart.id).delete()
            db.commit()


def test_checkout_with_delivery_zone_applies_fee_and_overwrites_district(client, db, checkout_setup):
    variant = checkout_setup["variant"]
    zone = checkout_setup["zone"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client, variant.id, guest_token, delivery_zone_id=str(zone.id), delivery_fee=zone.fee
    )
    assert response.status_code == 200
    order = unwrap(response)
    assert order["delivery_zone_id"] == str(zone.id)
    assert order["delivery_fee"] == zone.fee
    assert order["district"] == zone.name  # server overwrites with the zone's own name
    assert order["subtotal"] == 1000.0
    assert order["total"] == 1000.0 + zone.fee

    _cleanup_order(db, order["id"], guest_token)


def test_checkout_rejects_stale_delivery_fee(client, db, checkout_setup):
    variant = checkout_setup["variant"]
    zone = checkout_setup["zone"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    # Frontend thinks the fee is still zone.fee - 500 (e.g. it hasn't
    # refreshed since staff last edited the zone) - server must catch this,
    # never just trust the client's number.
    response = _checkout(
        client, variant.id, guest_token, delivery_zone_id=str(zone.id), delivery_fee=zone.fee - 500
    )
    assert response.status_code == 422

    # Checkout aborted before converting the cart - clean up the leftover
    # guest cart/item directly.
    cart = db.query(Cart).filter(Cart.guest_token == guest_token).first()
    if cart is not None:
        db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
        db.commit()
        db.query(Cart).filter(Cart.id == cart.id).delete()
        db.commit()


def test_checkout_without_delivery_zone_is_a_pickup_order(client, db, checkout_setup):
    variant = checkout_setup["variant"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(client, variant.id, guest_token)
    assert response.status_code == 200
    order = unwrap(response)
    assert order["delivery_zone_id"] is None
    assert order["delivery_fee"] == 0
    assert order["district"] == "whatever the frontend last had cached"
    assert order["total"] == 1000.0

    _cleanup_order(db, order["id"], guest_token)
