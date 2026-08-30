# Covers the Kampala delivery/pickup rework (2026-08-30 UAT, replacing the
# flat DeliveryZone shipped earlier the same day) - admin CRUD for all
# three new entities (DeliveryDivision, DeliveryArea, RegionalPickupStation),
# their public storefront listings, and the three checkout paths
# (Kampala pickup / Kampala door-to-door / outside-Kampala pickup) that
# routers/orders.py's checkout() now resolves between.

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import (
    Cart,
    CartItem,
    Category,
    DeliveryArea,
    DeliveryDivision,
    InventoryMovement,
    InventoryRecord,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    RegionalPickupStation,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_token(db):
    staff = StaffUser(
        full_name="Delivery Test Owner",
        email=f"deliveryowner-{uuid.uuid4().hex[:8]}@example.com",
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
        full_name="Delivery Test Attendant",
        email=f"deliveryattendant-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.delete(staff)
    db.commit()


def _cleanup_division(db, division_id):
    db.query(DeliveryArea).filter(DeliveryArea.division_id == uuid.UUID(division_id)).delete()
    db.commit()
    db.query(DeliveryDivision).filter(DeliveryDivision.id == uuid.UUID(division_id)).delete()
    db.commit()


def _cleanup_station(db, station_id):
    db.query(RegionalPickupStation).filter(RegionalPickupStation.id == uuid.UUID(station_id)).delete()
    db.commit()


def test_sales_attendant_rejected_from_every_admin_endpoint(client, db, sales_attendant_token):
    # Nyson's spec: "Access: Owner + System Administrator only" - unlike
    # most of this project's other 2026-08-18-widened modules, Sales
    # Attendant is NOT included in any of the three entities here.
    headers = _auth(sales_attendant_token)

    assert client.get("/api/v1/admin/delivery-divisions", headers=headers).status_code == 403
    assert (
        client.post("/api/v1/admin/delivery-divisions", json={"name": "X"}, headers=headers).status_code
        == 403
    )
    assert client.get("/api/v1/admin/delivery-areas", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/delivery-areas",
            json={"division_id": str(uuid.uuid4()), "name": "X", "fee": 1000},
            headers=headers,
        ).status_code
        == 403
    )
    assert client.get("/api/v1/admin/regional-pickup-stations", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/regional-pickup-stations",
            json={"major_town": "X", "address": "X", "fee": 1000, "contact": "X"},
            headers=headers,
        ).status_code
        == 403
    )


def test_division_admin_crud_and_status_toggle(client, db, owner_token):
    name = f"Nakawa {uuid.uuid4().hex[:8]}"
    create_response = client.post(
        "/api/v1/admin/delivery-divisions", json={"name": name}, headers=_auth(owner_token)
    )
    assert create_response.status_code == 201
    division = unwrap(create_response)
    assert division["is_active"] is True
    division_id = division["id"]

    public_list = unwrap(client.get("/api/v1/delivery-divisions"))
    assert any(d["id"] == division_id for d in public_list)

    update_response = client.patch(
        f"/api/v1/admin/delivery-divisions/{division_id}",
        json={"name": f"{name} Renamed"},
        headers=_auth(owner_token),
    )
    assert update_response.status_code == 200
    assert unwrap(update_response)["name"] == f"{name} Renamed"

    status_response = client.patch(
        f"/api/v1/admin/delivery-divisions/{division_id}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert status_response.status_code == 200
    assert unwrap(status_response)["is_active"] is False

    public_list_after = unwrap(client.get("/api/v1/delivery-divisions"))
    assert not any(d["id"] == division_id for d in public_list_after)

    admin_list = unwrap(client.get("/api/v1/admin/delivery-divisions", headers=_auth(owner_token)))
    assert any(d["id"] == division_id for d in admin_list)

    _cleanup_division(db, division_id)


def test_area_admin_crud_status_toggle_and_division_name(client, db, owner_token):
    division = DeliveryDivision(name=f"Central {uuid.uuid4().hex[:8]}")
    db.add(division)
    db.commit()

    create_response = client.post(
        "/api/v1/admin/delivery-areas",
        json={"division_id": str(division.id), "name": "Ntinda", "fee": 5000},
        headers=_auth(owner_token),
    )
    assert create_response.status_code == 201
    area = unwrap(create_response)
    assert area["fee"] == 5000
    assert area["division_name"] == division.name
    area_id = area["id"]

    # Public read requires division_id and only returns active areas for it.
    public_list = unwrap(client.get(f"/api/v1/delivery-areas?division_id={division.id}"))
    assert any(a["id"] == area_id for a in public_list)
    assert client.get("/api/v1/delivery-areas").status_code == 422  # division_id required

    update_response = client.patch(
        f"/api/v1/admin/delivery-areas/{area_id}", json={"fee": 7500}, headers=_auth(owner_token)
    )
    assert update_response.status_code == 200
    assert unwrap(update_response)["fee"] == 7500

    status_response = client.patch(
        f"/api/v1/admin/delivery-areas/{area_id}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert status_response.status_code == 200
    assert unwrap(status_response)["is_active"] is False

    public_list_after = unwrap(client.get(f"/api/v1/delivery-areas?division_id={division.id}"))
    assert not any(a["id"] == area_id for a in public_list_after)

    admin_list = unwrap(
        client.get(f"/api/v1/admin/delivery-areas?division_id={division.id}", headers=_auth(owner_token))
    )
    assert any(a["id"] == area_id for a in admin_list)

    unknown_division_response = client.post(
        "/api/v1/admin/delivery-areas",
        json={"division_id": str(uuid.uuid4()), "name": "Nowhere", "fee": 1000},
        headers=_auth(owner_token),
    )
    assert unknown_division_response.status_code == 404

    _cleanup_division(db, str(division.id))


def test_station_admin_crud_and_status_toggle(client, db, owner_token):
    create_response = client.post(
        "/api/v1/admin/regional-pickup-stations",
        json={"major_town": "Jinja", "address": "Main Street depot", "fee": 10000, "contact": "0700000000"},
        headers=_auth(owner_token),
    )
    assert create_response.status_code == 201
    station = unwrap(create_response)
    assert station["fee"] == 10000
    station_id = station["id"]

    public_list = unwrap(client.get("/api/v1/regional-pickup-stations"))
    assert any(s["id"] == station_id for s in public_list)

    update_response = client.patch(
        f"/api/v1/admin/regional-pickup-stations/{station_id}",
        json={"fee": 12000},
        headers=_auth(owner_token),
    )
    assert update_response.status_code == 200
    assert unwrap(update_response)["fee"] == 12000

    status_response = client.patch(
        f"/api/v1/admin/regional-pickup-stations/{station_id}/status",
        json={"is_active": False},
        headers=_auth(owner_token),
    )
    assert status_response.status_code == 200
    assert unwrap(status_response)["is_active"] is False

    public_list_after = unwrap(client.get("/api/v1/regional-pickup-stations"))
    assert not any(s["id"] == station_id for s in public_list_after)

    admin_list = unwrap(
        client.get("/api/v1/admin/regional-pickup-stations", headers=_auth(owner_token))
    )
    assert any(s["id"] == station_id for s in admin_list)

    _cleanup_station(db, station_id)


@pytest.fixture
def checkout_setup(db):
    category = Category(
        name=f"Delivery Checkout Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Delivery Checkout Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()
    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
    db.add(inventory)
    division = DeliveryDivision(name=f"Checkout Division {uuid.uuid4().hex[:8]}")
    db.add(division)
    db.flush()
    area = DeliveryArea(division_id=division.id, name="Checkout Area", fee=2000)
    db.add(area)
    station = RegionalPickupStation(
        major_town="Checkout Town", address="Depot Road", fee=8000, contact="0700000000"
    )
    db.add(station)
    db.commit()

    yield {"variant": variant, "division": division, "area": area, "station": station}

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
    db.query(DeliveryArea).filter(DeliveryArea.id == area.id).delete()
    db.commit()
    db.query(DeliveryDivision).filter(DeliveryDivision.id == division.id).delete()
    db.commit()
    db.query(RegionalPickupStation).filter(RegionalPickupStation.id == station.id).delete()
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
            "guest_full_name": "Delivery Buyer",
            "guest_phone_number": "+256700000098",
            "delivery_address": "123 Test Street",
            "district": "whatever the frontend computed",
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
        cart = db.query(Cart).filter(Cart.guest_token == guest_token).first()
        if cart is not None:
            db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
            db.commit()
            db.query(Cart).filter(Cart.id == cart.id).delete()
            db.commit()


def _cleanup_aborted_cart(db, guest_token):
    cart = db.query(Cart).filter(Cart.guest_token == guest_token).first()
    if cart is not None:
        db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
        db.commit()
        db.query(Cart).filter(Cart.id == cart.id).delete()
        db.commit()


def test_checkout_kampala_delivery_applies_area_fee_and_snapshots_names(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    division = checkout_setup["division"]
    area = checkout_setup["area"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        delivery_division_id=str(division.id),
        delivery_area_id=str(area.id),
        delivery_fee=area.fee,
    )
    assert response.status_code == 200
    order = unwrap(response)
    assert order["delivery_division_id"] == str(division.id)
    assert order["delivery_area_id"] == str(area.id)
    assert order["regional_pickup_station_id"] is None
    assert order["delivery_fee"] == area.fee
    assert order["delivery_division_name"] == division.name
    assert order["delivery_area_name"] == area.name
    assert order["pickup_town"] is None
    # district/delivery_address are trusted as sent, NOT overwritten -
    # unlike the old DeliveryZone flow.
    assert order["district"] == "whatever the frontend computed"
    assert order["total"] == 1000.0 + area.fee

    _cleanup_order(db, order["id"], guest_token)


def test_checkout_rejects_stale_area_fee(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    division = checkout_setup["division"]
    area = checkout_setup["area"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        delivery_division_id=str(division.id),
        delivery_area_id=str(area.id),
        delivery_fee=area.fee - 500,
    )
    assert response.status_code == 422

    _cleanup_aborted_cart(db, guest_token)


def test_checkout_outside_kampala_pickup_applies_station_fee_and_snapshots_town(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    station = checkout_setup["station"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        regional_pickup_station_id=str(station.id),
        delivery_fee=station.fee,
    )
    assert response.status_code == 200
    order = unwrap(response)
    assert order["regional_pickup_station_id"] == str(station.id)
    assert order["delivery_division_id"] is None
    assert order["delivery_area_id"] is None
    assert order["delivery_fee"] == station.fee
    assert order["pickup_town"] == station.major_town
    assert order["delivery_division_name"] is None
    assert order["total"] == 1000.0 + station.fee

    _cleanup_order(db, order["id"], guest_token)


def test_checkout_rejects_stale_station_fee(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    station = checkout_setup["station"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        regional_pickup_station_id=str(station.id),
        delivery_fee=station.fee - 500,
    )
    assert response.status_code == 422

    _cleanup_aborted_cart(db, guest_token)


def test_checkout_kampala_pickup_with_no_ids_is_zero_fee(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(client, variant.id, guest_token)
    assert response.status_code == 200
    order = unwrap(response)
    assert order["delivery_division_id"] is None
    assert order["delivery_area_id"] is None
    assert order["regional_pickup_station_id"] is None
    assert order["delivery_fee"] == 0
    assert order["district"] == "whatever the frontend computed"
    assert order["total"] == 1000.0

    _cleanup_order(db, order["id"], guest_token)


def test_checkout_rejects_area_without_matching_division(client, db, checkout_setup, mock_email):
    # area_id belongs to a DIFFERENT division than the one sent - a
    # malformed/tampered combination, not a legitimate path.
    variant = checkout_setup["variant"]
    area = checkout_setup["area"]
    other_division = DeliveryDivision(name=f"Other Division {uuid.uuid4().hex[:8]}")
    db.add(other_division)
    db.commit()
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        delivery_division_id=str(other_division.id),
        delivery_area_id=str(area.id),
        delivery_fee=area.fee,
    )
    assert response.status_code == 422

    _cleanup_aborted_cart(db, guest_token)
    db.query(DeliveryDivision).filter(DeliveryDivision.id == other_division.id).delete()
    db.commit()


def test_checkout_rejects_area_and_station_both_selected(client, db, checkout_setup, mock_email):
    variant = checkout_setup["variant"]
    division = checkout_setup["division"]
    area = checkout_setup["area"]
    station = checkout_setup["station"]
    guest_token = f"guest-{uuid.uuid4().hex}"

    response = _checkout(
        client,
        variant.id,
        guest_token,
        delivery_division_id=str(division.id),
        delivery_area_id=str(area.id),
        regional_pickup_station_id=str(station.id),
        delivery_fee=area.fee,
    )
    assert response.status_code == 422

    _cleanup_aborted_cart(db, guest_token)
