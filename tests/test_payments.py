# Covers the Payments phase: initiating a payment attempt against the REAL
# PesaPal gateway, listing/reading, the webhook confirming it (idempotent
# per PAYINT-005), the duplicate-payment rule (BR-PAY-005), and who's
# allowed to see what.
#
# pesapal_client's actual HTTP calls are mocked here (see mock_pesapal
# below) - this suite verifies OUR logic (status mapping, DB writes,
# idempotency, ownership), not PesaPal's sandbox itself. That gets
# verified once, separately, against real sandbox credentials - not on
# every test run, which shouldn't depend on network access or secrets.

import uuid
from datetime import timedelta

import pytest

from conftest import unwrap
from models import (
    Branch,
    Category,
    Customer,
    InventoryRecord,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductVariant,
    PaymentStatus,
    StaffRole,
    StaffUser,
)
from routers.payments import _map_pesapal_status
from security import create_access_token, hash_password


@pytest.fixture
def mock_pesapal(monkeypatch):
    # Patched where it's USED (routers.payments), not where it's defined
    # (pesapal_client) - routers/payments.py already imported these names
    # directly, so patching pesapal_client itself wouldn't affect the
    # reference routers/payments.py is holding.
    #
    # status_responses lets each test control what "PesaPal" reports back
    # for a given order_tracking_id before hitting the webhook - defaults
    # to FAILED for anything a test didn't explicitly set.
    status_responses = {}

    def fake_submit_order_request(merchant_reference, amount, currency, description, billing_email, billing_phone, billing_first_name, billing_last_name):
        return {
            "order_tracking_id": f"PESAPAL-{merchant_reference}",
            "redirect_url": f"https://cybqa.pesapal.com/pesapalv3/mock-checkout/{merchant_reference}",
        }

    def fake_get_transaction_status(order_tracking_id):
        return status_responses.get(order_tracking_id, {"payment_status_description": "FAILED"})

    # Also patched - there are no real PesaPal credentials in this dev/test
    # environment (see test_initiate_payment_returns_503_when_not_configured
    # below for the genuine, un-mocked behavior), so without this every
    # test here would hit PaymentsUnavailableError before ever reaching the
    # fakes above.
    monkeypatch.setattr("routers.payments.is_configured", lambda: True)
    monkeypatch.setattr("routers.payments.submit_order_request", fake_submit_order_request)
    monkeypatch.setattr("routers.payments.get_transaction_status", fake_get_transaction_status)

    return status_responses


@pytest.fixture
def order_setup(db):
    # Builds one real order directly via the DB rather than through the
    # cart/checkout API - checkout itself is already covered by the Orders
    # phase's own testing, so this only needs a valid order to attach
    # payments to, not to re-prove checkout works.
    category = Category(name=f"Test Category {uuid.uuid4().hex[:8]}")
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=50000.0)
    db.add(variant)
    db.flush()

    branch = Branch(name="Test Branch", address="123 Test Street")
    db.add(branch)
    db.flush()

    inventory = InventoryRecord(variant_id=variant.id, branch_id=branch.id, quantity_available=10)
    db.add(inventory)

    owner = Customer(
        full_name="Payer One",
        email=f"payer-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    other_customer = Customer(
        full_name="Not The Payer",
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add_all([owner, other_customer])
    db.flush()

    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=owner.id,
        fulfilling_branch_id=branch.id,
        guest_full_name=owner.full_name,
        guest_phone_number="+256700000000",
        delivery_address="Test Address",
        subtotal=50000.0,
        total=50000.0,
    )
    db.add(order)
    db.flush()

    db.add(
        OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            product_name_snapshot=product.name,
            variant_label_snapshot=None,
            quantity=1,
            unit_price=50000.0,
            line_total=50000.0,
        )
    )
    db.commit()

    owner_token = create_access_token(subject=str(owner.id), account_type="customer")
    other_token = create_access_token(subject=str(other_customer.id), account_type="customer")

    yield {"order": order, "owner_token": owner_token, "other_token": other_token}

    # Teardown in FK-dependency order - this project has no ORM
    # relationship() wiring, so SQLAlchemy can't infer delete order itself
    # (see CLAUDE.md); each table needs its own commit before the table it
    # points to is deleted.
    db.query(Payment).filter(Payment.order_id == order.id).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.order_id == order.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == inventory.id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id.in_([owner.id, other_customer.id])).delete(synchronize_session=False)
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()
    db.query(Branch).filter(Branch.id == branch.id).delete()
    db.commit()


@pytest.fixture
def staff_token(db):
    staff = StaffUser(
        full_name="Test Staff",
        email=f"staff-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()

    token = create_access_token(subject=str(staff.id), account_type="staff")
    yield token

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _webhook_call(client, tracking_id, merchant_reference):
    # The webhook is a GET with PesaPal's exact PascalCase query param
    # names (see routers/payments.py) - not a JSON body like the old
    # simulated webhook used.
    return client.get(
        "/api/v1/payments/webhook",
        params={"OrderTrackingId": tracking_id, "OrderMerchantReference": merchant_reference},
    )


def test_payment_lifecycle(client, order_setup, mock_pesapal):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201
    payment = unwrap(response)
    assert payment["status"] == "awaiting_payment"
    assert payment["provider_reference"] is not None
    assert payment["redirect_url"] is not None

    response = client.get(f"/api/v1/orders/{order.id}/payments", headers=headers)
    assert response.status_code == 200
    assert len(unwrap(response)) == 1

    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert response.status_code == 200

    # "PesaPal" (mocked) reports this one as completed - "Completed" Title
    # Case, matching what PesaPal's REAL sandbox actually sends (confirmed
    # via a live end-to-end test), not the "COMPLETED" all-caps the docs'
    # examples show. Using the real casing here is what would have caught
    # the case-sensitivity bug _map_pesapal_status had before it shipped.
    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}

    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200
    # The webhook's own response is PesaPal's required ack shape, NOT our
    # usual envelope and NOT the updated payment - verify the real effect
    # via a normal GET afterward.
    assert response.json()["status"] == 200

    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert unwrap(response)["status"] == "paid"

    # Idempotent: a repeated callback for the same reference is a no-op
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200
    assert response.json()["status"] == 200
    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert unwrap(response)["status"] == "paid"

    # A second attempt on an order that's already paid is rejected
    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 409


def test_payment_confirmed_email_sent_when_webhook_marks_paid(client, db, order_setup, mock_pesapal, mock_email):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    # order_setup's Order has no guest_email set (see that fixture) - set
    # one here directly, since send_payment_confirmed_email only fires
    # when there's actually somewhere to send it (same optional-email
    # reasoning as send_order_confirmation_email).
    order.guest_email = "payer-inbox@example.com"
    db.commit()

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200

    confirmation = next((e for e in mock_email if e["to_email"] == "payer-inbox@example.com"), None)
    assert confirmation is not None
    assert order.order_number in confirmation["subject"]
    assert order.order_number in confirmation["body"]
    assert order.order_number in confirmation["html"]


def test_no_payment_confirmed_email_when_webhook_marks_failed(client, db, order_setup, mock_pesapal, mock_email):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])
    order.guest_email = "payer-inbox@example.com"
    db.commit()

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Failed"}
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200

    assert not any(e["to_email"] == "payer-inbox@example.com" for e in mock_email)


def test_webhook_failed_payment(client, order_setup, mock_pesapal):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Failed"}

    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200
    assert response.json()["status"] == 200

    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    body = unwrap(response)
    assert body["status"] == "failed"
    assert body["failure_reason"] == "Payment failed"

    # A failed attempt doesn't block a new attempt on the same order
    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201


def test_second_payment_blocked_while_first_still_pending(client, order_setup, mock_pesapal):
    # Prevents a real double-charge risk: without this, a double-click (or
    # a retried request) could open a SECOND real PesaPal checkout session
    # for the same order while the first is still unresolved. Our DB-level
    # uniqueness only stops us from RECORDING two successful payments - it
    # does nothing to stop PesaPal from actually taking the customer's
    # money twice if they went on to complete both.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201

    # A second attempt while the first is still awaiting_payment is
    # rejected with a DIFFERENT error code than "already paid" - the
    # frontend needs to tell a customer "wait, one's already in progress"
    # apart from "this order is already paid for".
    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "PAYMENT_IN_PROGRESS"


def test_stale_pending_payment_does_not_block_retry(client, order_setup, mock_pesapal, db):
    # The block above has a deliberate time limit (PENDING_PAYMENT_WINDOW_MINUTES,
    # routers/payments.py) - a customer whose first attempt genuinely
    # stalled (closed the tab, lost connection) must still be able to
    # retry eventually, not be locked out of paying at all.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    first_payment_id = unwrap(response)["id"]

    # Simulate time passing by directly backdating initiated_at, rather
    # than actually waiting - this test needs to run in seconds, not
    # PENDING_PAYMENT_WINDOW_MINUTES real minutes.
    stale_payment = db.get(Payment, uuid.UUID(first_payment_id))
    stale_payment.initiated_at = stale_payment.initiated_at - timedelta(minutes=20)
    db.commit()

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201


def test_map_pesapal_status_is_case_insensitive():
    # Regression test, as a plain unit test (no HTTP/DB needed - this is
    # pure string-mapping logic) - a live sandbox test found PesaPal
    # actually sends "Completed" (Title Case), not the "COMPLETED" the
    # docs' examples show. _map_pesapal_status normalizes with .upper()
    # before comparing; this pins that behavior for every casing PesaPal
    # might plausibly send, so it can't silently regress to exact-match.
    for casing in ("COMPLETED", "Completed", "completed"):
        status, reason = _map_pesapal_status(casing)
        assert status == PaymentStatus.PAID, f"casing {casing!r} should map to PAID"
        assert reason is None

    for casing in ("FAILED", "Failed", "INVALID", "Invalid", "REVERSED", "Reversed"):
        status, _ = _map_pesapal_status(casing)
        assert status == PaymentStatus.FAILED, f"casing {casing!r} should map to FAILED"


def test_webhook_unknown_tracking_id_acks_with_failure_status(client, order_setup):
    # PesaPal doesn't want a raw 404 here - just its ack shape with
    # status: 500 so it knows something didn't match on our end.
    response = _webhook_call(client, "no-such-tracking-id", "whatever")
    assert response.status_code == 200
    assert response.json()["status"] == 500


def test_initiate_payment_returns_503_when_not_configured(client, order_setup, monkeypatch):
    # Explicitly forced False here, rather than relying on the ambient
    # .env lacking credentials - this environment now DOES have real
    # sandbox credentials configured (see .env), so without this the test
    # would silently make a real network call to PesaPal on every pytest
    # run instead of testing the "coming soon" degradation path at all.
    monkeypatch.setattr("routers.payments.is_configured", lambda: False)

    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "PAYMENTS_UNAVAILABLE"
    assert "soon" in body["message"].lower()


def test_payment_requires_ownership(client, order_setup):
    order = order_setup["order"]
    other_headers = _auth(order_setup["other_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=other_headers,
    )
    assert response.status_code == 404


def test_staff_can_manage_any_orders_payments(client, order_setup, staff_token, mock_pesapal):
    order = order_setup["order"]
    headers = _auth(staff_token)

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "cash_on_delivery", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201

    response = client.get(f"/api/v1/orders/{order.id}/payments", headers=headers)
    assert response.status_code == 200
    assert len(unwrap(response)) == 1
