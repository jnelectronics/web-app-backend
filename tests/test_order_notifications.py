# Covers the two background jobs spec'd in docs/JN_API_Specification.md's
# checkout sequence diagram (§4.2): send_order_placed_email (customer-
# facing) and notify_staff_new_order (every active staff member).
#
# NEITHER fires from checkout anymore (2026-09-16, "must be paid before
# it's placed") - checkout() only creates an order in awaiting_payment;
# both jobs now fire from routers/payments.py's _apply_pesapal_outcome,
# the moment PesaPal actually confirms payment. So most tests here drive a
# real checkout THROUGH a mocked-but-confirmed PesaPal payment (via
# conftest.py's mock_pesapal fixture and pay_order helper) before checking
# for an email at all - see jobs.py for what each actually sends.
#
# email_client's real Resend call is mocked here (mock_email, defined in
# conftest.py) - same idea as every other suite that triggers a real email
# job. TestClient runs BackgroundTasks to completion before client.post()
# returns, so a plain assertion right after the call works.

import uuid

import pytest

from conftest import pay_order, uncategorized_group_id, unwrap
from models import (
    Cart,
    CartItem,
    Category,
    Customer,
    InventoryMovement,
    InventoryRecord,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def checkout_setup(db):
    category = Category(name=f"Notify Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Notify Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=50000.0)
    db.add(variant)
    db.flush()

    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
    db.add(inventory)

    customer = Customer(
        full_name="Notify Test Customer",
        email=f"notifycust-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add(customer)
    db.commit()

    token = create_access_token(subject=str(customer.id), account_type="customer")

    yield {"variant": variant, "customer": customer, "token": token}

    # Teardown in FK-dependency order (see CLAUDE.md) - checkout() creates
    # Order/OrderItem/InventoryMovement rows this fixture's setup never
    # touched, so they have to be cleaned up first, before the rows they
    # point at.
    order_ids = [o.id for o in db.query(Order).filter(Order.customer_id == customer.id).all()]
    if order_ids:
        # Payment rows exist now too - tests here drive a real payment via
        # conftest.py's pay_order helper (2026-09-16), not just checkout.
        db.query(Payment).filter(Payment.order_id.in_(order_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(InventoryMovement).filter(InventoryMovement.order_id.in_(order_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
        db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).delete()
    db.commit()
    # checkout() also creates a Cart (POST /cart/items, via get_current_cart)
    # pointing at this customer - has to go before the customer too, same
    # FK-safe-order gotcha as Order/OrderItem above.
    cart_ids = [c.id for c in db.query(Cart).filter(Cart.customer_id == customer.id).all()]
    if cart_ids:
        db.query(CartItem).filter(CartItem.cart_id.in_(cart_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Cart).filter(Cart.id.in_(cart_ids)).delete(synchronize_session=False)
        db.commit()
    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


@pytest.fixture
def active_staff(db):
    staff = StaffUser(
        full_name="Active Notify Staff",
        email=f"activestaff-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()
    yield staff
    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


@pytest.fixture
def inactive_staff(db):
    staff = StaffUser(
        full_name="Inactive Notify Staff",
        email=f"inactivestaff-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
        is_active=False,
    )
    db.add(staff)
    db.commit()
    yield staff
    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _add_to_cart(client, token, variant_id, quantity=1):
    response = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(variant_id), "quantity": quantity},
        headers=_auth(token),
    )
    assert response.status_code == 200


def test_checkout_409_reports_which_items_are_short(client, checkout_setup, active_staff, mock_email):
    # checkout_setup's InventoryRecord has 10 units available - requesting
    # more than that means there isn't enough stock to fulfill it.
    _add_to_cart(client, checkout_setup["token"], checkout_setup["variant"].id, quantity=999)

    response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Notify Test Customer",
            "guest_phone_number": "+256700000004",
            "delivery_address": "1 Test Way, Kampala",
            "district": "Test District",
        },
        headers=_auth(checkout_setup["token"]),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "INSUFFICIENT_INVENTORY"
    assert body["short_items"] == [
        {"variant_id": str(checkout_setup["variant"].id), "requested": 999, "available": 10}
    ]


def test_checkout_alone_sends_no_email_and_order_stays_awaiting_payment(
    client, db, checkout_setup, active_staff, mock_email
):
    # THE core regression test for this file, added 2026-09-16 alongside
    # "must be paid before it's placed": checkout on its own - no payment
    # attempted at all - must not email anyone or place a real order. This
    # is the exact bug Norman found in real UAT testing: abandoning
    # PesaPal's page used to still leave a placed order + a sent "Order
    # Placed" email behind, because that email used to fire right here, at
    # checkout, before payment had even been attempted.
    _add_to_cart(client, checkout_setup["token"], checkout_setup["variant"].id)

    response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Notify Test Customer",
            "guest_phone_number": "+256700000005",
            "guest_email": "customer-inbox@example.com",
            "delivery_address": "1 Test Way, Kampala",
            "district": "Test District",
        },
        headers=_auth(checkout_setup["token"]),
    )
    assert response.status_code == 200
    order = unwrap(response)
    assert order["status"] == "awaiting_payment"

    assert mock_email == []

    db_order = db.get(Order, uuid.UUID(order["id"]))
    assert db_order.status == OrderStatus.AWAITING_PAYMENT
    # Stock untouched either - see routers/orders.py's checkout comment.
    inventory = db.query(InventoryRecord).filter(InventoryRecord.variant_id == checkout_setup["variant"].id).first()
    assert inventory.quantity_available == 10


def test_checkout_sends_order_confirmation_when_email_given(
    client, checkout_setup, active_staff, mock_email, mock_pesapal
):
    _add_to_cart(client, checkout_setup["token"], checkout_setup["variant"].id)

    response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Notify Test Customer",
            "guest_phone_number": "+256700000001",
            "guest_email": "customer-inbox@example.com",
            "delivery_address": "1 Test Way, Kampala",
            "district": "Test District",
        },
        headers=_auth(checkout_setup["token"]),
    )
    assert response.status_code == 200
    order = unwrap(response)

    # Nothing sent yet - only once payment is confirmed does this fire.
    assert mock_email == []
    pay_order(client, order["id"], mock_pesapal, headers=_auth(checkout_setup["token"]))

    confirmation = next((e for e in mock_email if e["to_email"] == "customer-inbox@example.com"), None)
    assert confirmation is not None
    assert order["order_number"] in confirmation["subject"]
    assert order["order_number"] in confirmation["body"]
    assert order["order_number"] in confirmation["html"]


def test_checkout_skips_confirmation_when_no_email_given(
    client, checkout_setup, active_staff, mock_email, mock_pesapal
):
    _add_to_cart(client, checkout_setup["token"], checkout_setup["variant"].id)

    response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Notify Test Customer",
            "guest_phone_number": "+256700000002",
            "delivery_address": "1 Test Way, Kampala",
            "district": "Test District",
        },
        headers=_auth(checkout_setup["token"]),
    )
    assert response.status_code == 200
    order = unwrap(response)
    pay_order(client, order["id"], mock_pesapal, headers=_auth(checkout_setup["token"]))

    # No guest_email given - nothing addressed to a customer inbox, only
    # the staff notification (active_staff's address) should be present.
    assert not any(e["subject"].startswith("Your JN Electronics Order") for e in mock_email)
    assert any(e["to_email"] == active_staff.email for e in mock_email)


def test_checkout_notifies_only_active_staff(
    client, checkout_setup, active_staff, inactive_staff, mock_email, mock_pesapal
):
    _add_to_cart(client, checkout_setup["token"], checkout_setup["variant"].id)

    response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Notify Test Customer",
            "guest_phone_number": "+256700000003",
            "delivery_address": "1 Test Way, Kampala",
            "district": "Test District",
        },
        headers=_auth(checkout_setup["token"]),
    )
    assert response.status_code == 200
    order = unwrap(response)
    pay_order(client, order["id"], mock_pesapal, headers=_auth(checkout_setup["token"]))

    notified_emails = {e["to_email"] for e in mock_email if e["subject"] == f"New Order {order['order_number']} Placed"}
    assert active_staff.email in notified_emails
    assert inactive_staff.email not in notified_emails
