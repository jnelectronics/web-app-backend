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

from conftest import uncategorized_group_id, unwrap
from models import (
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
    PaymentStatus,
    StaffRole,
    StaffUser,
)
from routers.payments import _map_pesapal_status
from security import create_access_token, hash_password

# mock_pesapal now lives in conftest.py (moved 2026-09-16) - more than one
# test file needs to drive a real payment to completion now that every
# order must be paid before it's placed, not just this one.


@pytest.fixture
def order_setup(db):
    # Builds one real order directly via the DB rather than through the
    # cart/checkout API - checkout itself is already covered by the Orders
    # phase's own testing, so this only needs a valid order to attach
    # payments to, not to re-prove checkout works.
    category = Category(name=f"Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=50000.0)
    db.add(variant)
    db.flush()

    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
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
        guest_full_name=owner.full_name,
        guest_phone_number="+256700000000",
        delivery_address="Test Address",
        district="Test District",
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

    yield {
        "order": order,
        "owner_token": owner_token,
        "other_token": other_token,
        "variant": variant,
        "inventory": inventory,
    }

    # Teardown in FK-dependency order - this project has no ORM
    # relationship() wiring, so SQLAlchemy can't infer delete order itself
    # (see CLAUDE.md); each table needs its own commit before the table it
    # points to is deleted. InventoryMovement rows are new here as of
    # 2026-09-16 - a confirmed payment now logs a real SOLD movement
    # (routers/payments.py's _apply_pesapal_outcome), which this fixture
    # never produced before (it built the Order directly, bypassing
    # checkout) - must go before BOTH Order and InventoryRecord, since it
    # references both.
    db.query(Payment).filter(Payment.order_id == order.id).delete()
    db.commit()
    db.query(InventoryMovement).filter(InventoryMovement.order_id == order.id).delete()
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


@pytest.fixture
def owner_staff_token(db):
    # A real StaffRole.OWNER account - NOT to be confused with order_setup's
    # "owner_token", which is a customer token (the customer who owns the
    # order). Used by test_mark_paid_endpoint_removed below to confirm the
    # route is genuinely gone, not just newly forbidden to a lower role.
    staff = StaffUser(
        full_name="Test Owner",
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    token = create_access_token(subject=str(staff.id), account_type="staff")
    yield token

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


@pytest.fixture
def active_staff(db):
    # An active StaffUser to verify notify_staff_new_order actually reaches
    # (added 2026-09-16 - that job now fires from the payment-success path,
    # not checkout, so this file needs its own real recipient to check for,
    # same idea as test_order_notifications.py's identically-named fixture).
    staff = StaffUser(
        full_name="Active Payments Staff",
        email=f"activepaystaff-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()
    yield staff
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


def test_paid_payment_places_the_order_and_notifies_everyone(
    client, db, order_setup, mock_pesapal, mock_email, active_staff
):
    # THE core regression test for "must be paid before it's placed"
    # (2026-09-16): order_setup's Order starts in awaiting_payment (the
    # model default) with 10 units of stock and one real OrderItem (qty 1,
    # "Test Product") - nothing has happened to it yet. Only once PesaPal
    # confirms payment should it actually become a real, placed order.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])
    order.guest_email = "payer-inbox@example.com"
    db.commit()

    assert order.status == OrderStatus.AWAITING_PAYMENT

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money"},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200

    db.refresh(order)
    assert order.status == OrderStatus.PENDING

    # Stock decremented NOW, not at checkout - order_setup's InventoryRecord
    # started at 10, one unit was "sold".
    db.refresh(order_setup["inventory"])
    assert order_setup["inventory"].quantity_available == 9

    # The customer gets the SAME "Order Placed" email checkout used to send
    # immediately - it now only fires here, with the real item/subtotal/
    # total details (order_setup's one OrderItem: "Test Product" x1).
    customer_email = next((e for e in mock_email if e["to_email"] == "payer-inbox@example.com"), None)
    assert customer_email is not None
    assert "Placed" in customer_email["subject"]
    assert order.order_number in customer_email["subject"]
    assert "Test Product" in customer_email["body"]
    assert "Test Product" in customer_email["html"]

    # Every active staff member gets the upgraded admin notification, now
    # also carrying the item/subtotal/total breakdown (2026-09-16, Norman's
    # explicit request) - not just the order's grand total.
    staff_email = next((e for e in mock_email if e["to_email"] == active_staff.email), None)
    assert staff_email is not None
    assert f"New Order {order.order_number} Placed" == staff_email["subject"]
    assert "Test Product" in staff_email["body"]
    assert "Test Product" in staff_email["html"]
    assert "Subtotal" in staff_email["html"]


def test_no_order_placed_email_when_payment_fails(client, db, order_setup, mock_pesapal, mock_email):
    # Pins the exact bug Norman found in real UAT testing (2026-09-16):
    # proceeding to PesaPal's page and abandoning it without paying must
    # NOT produce an "Order Placed" email - under the old design it did,
    # because that email fired at checkout (before payment), not at
    # payment success. Now checkout doesn't email anyone at all, so a
    # failed/abandoned payment should leave mock_email completely empty.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])
    order.guest_email = "payer-inbox@example.com"
    db.commit()

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card"},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Failed"}
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200

    assert mock_email == []

    db.refresh(order)
    assert order.status == OrderStatus.AWAITING_PAYMENT
    # Stock was never touched by the failed attempt either.
    db.refresh(order_setup["inventory"])
    assert order_setup["inventory"].quantity_available == 10


def test_awaiting_payment_order_hidden_from_staff_list_until_paid(
    client, order_setup, staff_token, mock_pesapal
):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])
    staff_headers = _auth(staff_token)

    response = client.get("/api/v1/orders/staff", headers=staff_headers)
    order_ids = {o["id"] for o in unwrap(response)["items"]}
    assert str(order.id) not in order_ids

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money"},
        headers=headers,
    )
    payment = unwrap(response)
    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}
    _webhook_call(client, payment["provider_reference"], payment["id"])

    response = client.get("/api/v1/orders/staff", headers=staff_headers)
    order_ids = {o["id"] for o in unwrap(response)["items"]}
    assert str(order.id) in order_ids


def test_oversold_payment_still_succeeds(client, db, order_setup, mock_pesapal, caplog):
    # Norman's explicit call (2026-09-16): a customer who already paid real
    # money keeps their order even if two people happened to pay for the
    # last unit at nearly the same instant - refusing it now would mean an
    # awkward refund, which is worse than a rare oversold item. Simulated
    # here by draining stock to 0 BEFORE the payment confirms (the exact
    # shape a real race would leave behind), rather than actually racing
    # two requests against each other.
    order = order_setup["order"]
    order_setup["inventory"].quantity_available = 0
    db.commit()
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money"},
        headers=headers,
    )
    payment = unwrap(response)
    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}

    with caplog.at_level("WARNING"):
        response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200

    db.refresh(order)
    assert order.status == OrderStatus.PENDING  # placed anyway, not blocked
    db.refresh(order_setup["inventory"])
    # Clamped at 0, not left negative - InventoryRecord.quantity_available
    # has its own DB CHECK (>= 0) constraint (see routers/payments.py's
    # comment); the warning log is the actual oversell signal, not a
    # negative number in the row itself.
    assert order_setup["inventory"].quantity_available == 0
    assert "oversold" in caplog.text.lower()


def test_payment_provider_rejects_cash_on_delivery(client, order_setup):
    # cash_on_delivery removed entirely 2026-09-16 - every order must now
    # be paid via PesaPal (mobile money or card/bank), "regardless of
    # picking from the store, within or outside kampala" (Norman's own
    # words). No longer a valid PaymentProvider value at all.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "cash_on_delivery"},
        headers=headers,
    )
    assert response.status_code == 422


def test_mark_paid_endpoint_removed(client, order_setup, owner_staff_token):
    # PATCH /payments/{id}/mark-paid no longer exists at all - removed
    # alongside cash-on-delivery.
    response = client.patch(
        f"/api/v1/payments/{uuid.uuid4()}/mark-paid", headers=_auth(owner_staff_token)
    )
    assert response.status_code == 404


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


def test_read_payment_recheck_resolves_paid_when_webhook_missed(
    client, db, order_setup, mock_pesapal, mock_email
):
    # The scenario this whole feature exists for: PesaPal's IPN callback
    # never arrives (dropped, misconfigured, whatever) - the customer
    # actually paid, but nothing ever told us. GET /payments/{id} (the
    # exact endpoint the frontend polls per
    # docs/Frontend_Integration_Contract.md) should self-heal this the
    # next time anyone checks, WITHOUT the webhook ever being called here.
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])
    order.guest_email = "payer-inbox@example.com"
    db.commit()

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    payment = unwrap(response)
    assert payment["status"] == "awaiting_payment"

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}

    # No _webhook_call anywhere in this test - only the GET a real polling
    # frontend would make.
    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert unwrap(response)["status"] == "paid"

    # The same "Order Placed" email the webhook path sends still fires here
    # - a customer shouldn't miss it just because the recheck, not the
    # webhook, was what actually discovered the payment succeeded.
    confirmation = next((e for e in mock_email if e["to_email"] == "payer-inbox@example.com"), None)
    assert confirmation is not None
    assert order.order_number in confirmation["subject"]

    db.refresh(order)
    assert order.status == OrderStatus.PENDING


def test_read_payment_recheck_does_not_mark_failed(client, order_setup, mock_pesapal):
    # Deliberately the OPPOSITE of the "resolves paid" test above: PesaPal
    # has no genuine "still checking out" status, so a poll landing before
    # the customer has finished paying could map to FAILED via
    # _map_pesapal_status's fallback. The lazy recheck must NOT apply that
    # - only a real webhook callback is allowed to mark a payment failed
    # (see _apply_pesapal_outcome's own comment for the full reasoning).
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "card", "amount": 50000.0},
        headers=headers,
    )
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Invalid"}

    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    # Still awaiting_payment, NOT failed - the customer may still be
    # legitimately mid-checkout.
    assert unwrap(response)["status"] == "awaiting_payment"

    # The real webhook is still the one that gets to make this call.
    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Failed"}
    response = _webhook_call(client, payment["provider_reference"], payment["id"])
    assert response.status_code == 200
    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert unwrap(response)["status"] == "failed"


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
        json={"provider": "mobile_money"},
        headers=headers,
    )
    assert response.status_code == 201

    response = client.get(f"/api/v1/orders/{order.id}/payments", headers=headers)
    assert response.status_code == 200
    assert len(unwrap(response)) == 1


@pytest.fixture
def guest_order_setup(db):
    # A guest order built directly (not through a real cart/checkout flow -
    # that path is already covered elsewhere, e.g. test_order_notifications.py)
    # with a known guest_token, so payments' guest-authorization logic can
    # be tested in isolation.
    category = Category(name=f"Guest Pay Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Guest Pay Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=50000.0)
    db.add(variant)
    db.flush()

    inventory = InventoryRecord(variant_id=variant.id, quantity_available=10)
    db.add(inventory)

    guest_token = f"guest-{uuid.uuid4().hex}"
    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=None,
        guest_token=guest_token,
        guest_full_name="Guest Payer",
        guest_phone_number="+256700000099",
        delivery_address="Test Address",
        district="Test District",
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

    yield {"order": order, "guest_token": guest_token}

    db.query(Payment).filter(Payment.order_id == order.id).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.order_id == order.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == inventory.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_guest_can_pay_for_own_order(client, guest_order_setup, mock_pesapal):
    order = guest_order_setup["order"]
    headers = {"X-Guest-Token": guest_order_setup["guest_token"]}

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 201
    payment = unwrap(response)

    response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert response.status_code == 200

    response = client.get(f"/api/v1/orders/{order.id}/payments", headers=headers)
    assert response.status_code == 200
    assert len(unwrap(response)) == 1


def test_guest_cannot_pay_with_wrong_guest_token(client, guest_order_setup):
    order = guest_order_setup["order"]
    wrong_headers = {"X-Guest-Token": "not-the-real-token"}

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
        headers=wrong_headers,
    )
    assert response.status_code == 404


def test_payment_endpoints_require_some_credential(client, guest_order_setup):
    order = guest_order_setup["order"]
    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "mobile_money", "amount": 50000.0},
    )
    assert response.status_code == 401


def test_payment_provider_rejects_unknown_value(client, order_setup):
    order = order_setup["order"]
    headers = _auth(order_setup["owner_token"])

    response = client.post(
        f"/api/v1/orders/{order.id}/payments",
        json={"provider": "bitcoin", "amount": 50000.0},
        headers=headers,
    )
    assert response.status_code == 422


# test_cash_on_delivery_skips_pesapal_and_staff_can_mark_it_paid,
# test_mark_paid_rejects_non_cash_payments, and
# test_mark_paid_rejects_sales_attendant were removed 2026-09-16 alongside
# cash-on-delivery itself (see test_payment_provider_rejects_cash_on_delivery
# and test_mark_paid_endpoint_removed above, which pin its actual removal).
