# Covers staff-only order status advancement (FR-ORDER-012, the
# pending->confirmed->packed->out_for_delivery->delivered lifecycle) and
# its status-history log (FR-ORDER-013).

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import (
    AuditLog,
    Category,
    Customer,
    DeliveryArea,
    DeliveryDivision,
    Order,
    OrderStatusHistory,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


@pytest.fixture
def order_setup(db):
    # A genuine Kampala DOOR-TO-DOOR DELIVERY order - needs a real
    # DeliveryDivision/DeliveryArea row (an FK, not just an id) so that
    # is_kampala_store_pickup(order) is unambiguously False here, keeping
    # this fixture's regression coverage of the plain
    # pending->confirmed->packed->out_for_delivery->delivered lifecycle
    # meaningful. Kampala STORE pickup (all three ids None) gets its own
    # pickup_order_setup fixture below.
    category = Category(name=f"OS Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="OS Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()

    division = DeliveryDivision(name=f"OS Test Division {uuid.uuid4().hex[:8]}")
    db.add(division)
    db.flush()
    area = DeliveryArea(division_id=division.id, name=f"OS Test Area {uuid.uuid4().hex[:8]}", fee=5000)
    db.add(area)
    db.flush()

    owner = Customer(
        full_name="Order Owner",
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    other = Customer(
        full_name="Not The Owner",
        email=f"notowner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add_all([owner, other])
    db.flush()

    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=owner.id,
        guest_full_name=owner.full_name,
        guest_phone_number="+256700000000",
        delivery_address="Test Address",
        district="Test District",
        delivery_division_id=division.id,
        delivery_area_id=area.id,
        delivery_fee=area.fee,
        delivery_division_name=division.name,
        delivery_area_name=area.name,
        subtotal=1000.0,
        total=1000.0 + area.fee,
    )
    db.add(order)
    db.commit()

    tokens = {
        "owner": create_access_token(subject=str(owner.id), account_type="customer"),
        "other": create_access_token(subject=str(other.id), account_type="customer"),
    }

    yield {"order": order, "tokens": tokens}

    # order_status_history rows reference BOTH this order and whichever
    # staff advanced it - must go first, or deleting the order/staff below
    # hits a foreign key violation.
    db.query(OrderStatusHistory).filter(OrderStatusHistory.order_id == order.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id.in_([owner.id, other.id])).delete(synchronize_session=False)
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


@pytest.fixture
def pickup_order_setup(db):
    # A Kampala STORE PICKUP order - all three delivery-selection ids left
    # None, delivery_fee left at its 0 default - exactly the shape
    # routers/orders.py's is_kampala_store_pickup() detects. Separate
    # fixture from order_setup (a real delivery order) so each test's
    # intent is unambiguous from which fixture it asks for.
    category = Category(name=f"OS Pickup Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="OS Pickup Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()

    owner = Customer(
        full_name="Pickup Order Owner",
        email=f"pickupowner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add(owner)
    db.flush()

    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=owner.id,
        guest_full_name=owner.full_name,
        guest_phone_number="+256700000099",
        delivery_address="JN Electronics Kampala Store",
        district="Kampala",
        subtotal=1000.0,
        total=1000.0,
    )
    db.add(order)
    db.commit()

    token = create_access_token(subject=str(owner.id), account_type="customer")

    yield {"order": order, "token": token}

    db.query(OrderStatusHistory).filter(OrderStatusHistory.order_id == order.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id == owner.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


@pytest.fixture
def staff_tokens(db):
    created = {}
    for role in StaffRole:
        staff = StaffUser(
            full_name=f"OS Test {role.value}",
            email=f"{role.value}-os-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("Password123"),
            role=role,
        )
        db.add(staff)
        created[role] = staff
    db.commit()

    tokens = {role: create_access_token(subject=str(s.id), account_type="staff") for role, s in created.items()}
    yield tokens

    # pytest tears fixtures down in reverse setup order, so this runs
    # BEFORE order_setup's own teardown - any order_status_history row this
    # staff wrote (changed_by_staff_id) must be cleared here too, or
    # deleting the staff hits the same FK violation order_setup's teardown
    # is careful to avoid for the order side. Advancing a status also now
    # writes an audit_logs row (routers/orders.py calls write_audit_log) -
    # same reasoning, same fix.
    staff_ids = [s.id for s in created.values()]
    db.query(OrderStatusHistory).filter(OrderStatusHistory.changed_by_staff_id.in_(staff_ids)).delete(
        synchronize_session=False
    )
    db.commit()
    db.query(AuditLog).filter(AuditLog.staff_user_id.in_(staff_ids)).delete(synchronize_session=False)
    db.commit()
    for staff in created.values():
        db.delete(staff)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_advance_status_requires_sales_attendant_or_owner(client, order_setup, staff_tokens):
    order = order_setup["order"]

    # System Administrator isn't listed for this endpoint (docs: Sales
    # Attendant, Owner only) but is a superset role, so it
    # still gets through - pending -> confirmed.
    response = client.patch(
        f"/api/v1/orders/{order.id}/status",
        json={"to_status": "confirmed"},
        headers=_auth(staff_tokens[StaffRole.SYSTEM_ADMINISTRATOR]),
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "confirmed"

    # Sales Attendant can advance it further - confirmed -> packed.
    response = client.patch(
        f"/api/v1/orders/{order.id}/status",
        json={"to_status": "packed", "notes": "Stock verified"},
        headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT]),
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "packed"


def test_invalid_transition_is_rejected(client, order_setup, staff_tokens):
    order = order_setup["order"]

    # pending -> delivered skips the required intermediate steps
    response = client.patch(
        f"/api/v1/orders/{order.id}/status",
        json={"to_status": "delivered"},
        headers=_auth(staff_tokens[StaffRole.OWNER]),
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "INVALID_STATE_TRANSITION"


def test_status_history_visible_to_owner_and_staff_not_others(client, order_setup, staff_tokens):
    order = order_setup["order"]
    tokens = order_setup["tokens"]

    client.patch(
        f"/api/v1/orders/{order.id}/status",
        json={"to_status": "confirmed", "notes": "Stock verified"},
        headers=_auth(staff_tokens[StaffRole.OWNER]),
    )

    response = client.get(f"/api/v1/orders/{order.id}/status-history", headers=_auth(tokens["owner"]))
    assert response.status_code == 200
    history = unwrap(response)
    assert len(history) == 1
    assert history[0]["from_status"] == "pending"
    assert history[0]["to_status"] == "confirmed"
    assert history[0]["notes"] == "Stock verified"

    response = client.get(f"/api/v1/orders/{order.id}/status-history", headers=_auth(tokens["other"]))
    assert response.status_code == 404

    response = client.get(
        f"/api/v1/orders/{order.id}/status-history", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 200


def test_confirmed_out_for_delivery_and_delivered_emails_sent_with_rating_link(
    client, db, order_setup, staff_tokens, mock_email
):
    # 2026-08-30/31, client-requested: an email at each of these three
    # transitions - Confirmed is its own separate email from checkout's
    # "Order Placed" one (see jobs.py's send_order_confirmed_email), and
    # the Delivered one carries the "Rate your experience" link
    # (routers/order_ratings.py's public token flow).
    order = order_setup["order"]
    order.guest_email = "buyer@example.com"
    db.commit()

    owner_headers = _auth(staff_tokens[StaffRole.OWNER])

    client.patch(f"/api/v1/orders/{order.id}/status", json={"to_status": "confirmed"}, headers=owner_headers)
    confirmed_email = next((e for e in mock_email if e["to_email"] == "buyer@example.com"), None)
    assert confirmed_email is not None
    assert "Confirmed" in confirmed_email["subject"]

    client.patch(f"/api/v1/orders/{order.id}/status", json={"to_status": "packed"}, headers=owner_headers)
    assert len(mock_email) == 1  # packed itself sends nothing new

    response = client.patch(
        f"/api/v1/orders/{order.id}/status", json={"to_status": "out_for_delivery"}, headers=owner_headers
    )
    assert response.status_code == 200
    out_for_delivery_email = next(
        (e for e in mock_email if e["to_email"] == "buyer@example.com" and "Out for Delivery" in e["subject"]), None
    )
    assert out_for_delivery_email is not None
    assert order.delivery_address in out_for_delivery_email["body"]

    response = client.patch(
        f"/api/v1/orders/{order.id}/status", json={"to_status": "delivered"}, headers=owner_headers
    )
    assert response.status_code == 200
    delivered_emails = [
        e for e in mock_email if e["to_email"] == "buyer@example.com" and "Delivered" in e["subject"]
    ]
    assert len(delivered_emails) == 1
    assert "rate-order?token=" in delivered_emails[0]["body"]


def test_kampala_store_pickup_skips_out_for_delivery(client, db, pickup_order_setup, staff_tokens, mock_email):
    # Nyson, 2026-08-31: a Kampala store pickup order goes straight from
    # packed to delivered - out_for_delivery must not even be a reachable
    # transition for it, and the "delivered" email should be the pickup-
    # specific "Collected" one, not the door-to-door "Delivered" one.
    order = pickup_order_setup["order"]
    order.guest_email = "pickup-buyer@example.com"
    db.commit()

    owner_headers = _auth(staff_tokens[StaffRole.OWNER])

    client.patch(f"/api/v1/orders/{order.id}/status", json={"to_status": "confirmed"}, headers=owner_headers)
    client.patch(f"/api/v1/orders/{order.id}/status", json={"to_status": "packed"}, headers=owner_headers)

    # packed -> out_for_delivery is rejected for a Kampala pickup order.
    response = client.patch(
        f"/api/v1/orders/{order.id}/status", json={"to_status": "out_for_delivery"}, headers=owner_headers
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "INVALID_STATE_TRANSITION"

    # packed -> delivered (straight through) is allowed.
    response = client.patch(
        f"/api/v1/orders/{order.id}/status", json={"to_status": "delivered"}, headers=owner_headers
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "delivered"
    assert unwrap(response)["fulfillment_method"] == "pickup"
    assert unwrap(response)["location"] == "kampala"

    # The email sent on delivery is the pickup-specific "Collected" one,
    # not the door-to-door "Delivered" one (the confirmed-transition email
    # sent earlier is also addressed to this same inbox, hence checking
    # "any"/"not any" across everything sent rather than assuming a single
    # matching email).
    pickup_emails = [e for e in mock_email if e["to_email"] == "pickup-buyer@example.com"]
    assert any("Collected" in e["subject"] for e in pickup_emails)
    assert not any("Delivered" in e["subject"] for e in pickup_emails)
    assert any("rate-order?token=" in e["body"] for e in pickup_emails)

    # Status history never contains out_for_delivery - the rejected patch
    # above never wrote a row (advance_order_status raises before it does).
    history = unwrap(
        client.get(f"/api/v1/orders/{order.id}/status-history", headers=_auth(pickup_order_setup["token"]))
    )
    assert [h["to_status"] for h in history] == ["confirmed", "packed", "delivered"]


def test_no_status_change_email_without_guest_email(client, order_setup, staff_tokens, mock_email):
    # order_setup's Order has no guest_email set - a phone-only guest has
    # nothing for either job to send to, same gate checkout's own
    # confirmation email already uses.
    order = order_setup["order"]
    owner_headers = _auth(staff_tokens[StaffRole.OWNER])
    for to_status in ("confirmed", "packed", "out_for_delivery", "delivered"):
        response = client.patch(
            f"/api/v1/orders/{order.id}/status", json={"to_status": to_status}, headers=owner_headers
        )
        assert response.status_code == 200
    assert mock_email == []


def test_order_edit_widened_to_contact_fields(client, order_setup):
    order = order_setup["order"]
    tokens = order_setup["tokens"]

    response = client.patch(
        f"/api/v1/orders/{order.id}",
        json={
            "delivery_address": "New Address, Kampala",
            "guest_full_name": "Updated Name",
            "guest_phone_number": "+256711111111",
            "guest_email": "updated@example.com",
        },
        headers=_auth(tokens["owner"]),
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert body["delivery_address"] == "New Address, Kampala"
    assert body["guest_full_name"] == "Updated Name"
    assert body["guest_phone_number"] == "+256711111111"
    assert body["guest_email"] == "updated@example.com"

    # Omitting the contact fields leaves them as they are, not cleared
    response = client.patch(
        f"/api/v1/orders/{order.id}",
        json={"delivery_address": "Yet Another Address"},
        headers=_auth(tokens["owner"]),
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert body["delivery_address"] == "Yet Another Address"
    assert body["guest_full_name"] == "Updated Name"
