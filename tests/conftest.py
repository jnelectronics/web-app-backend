# Shared pytest fixtures. TestClient runs the real FastAPI app in-process
# (no uvicorn needed) - requests to it go through the actual routes,
# dependencies, and the real Neon database, same as testing via /docs would.

import pytest
from fastapi.testclient import TestClient

from database import SessionLocal
from main import app
from models import CategoryGroup


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    # A direct DB session, separate from the one each API request opens for
    # itself - lets a test set up/tear down rows the API has no endpoint
    # for (e.g. seeding inventory to an exact quantity for a scenario).
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def unwrap(response):
    # Every successful response now comes back wrapped in the standard
    # envelope ({"success", "message", "data"} - see envelope.py), so tests
    # need to reach into "data" to get the actual resource instead of
    # reading the top level directly.
    return response.json()["data"]


def uncategorized_group_id(db):
    # Category.category_group_id became required once category groups
    # were added (migration 9efc87f6f25a) - tests that only need SOME
    # valid category (not testing grouping itself) point at the same
    # "Uncategorized" group that migration seeds for real data, rather
    # than every test fixture spinning up (and tearing down) a throwaway
    # group of its own just to satisfy the NOT NULL constraint.
    group = db.query(CategoryGroup).filter(CategoryGroup.name == "Uncategorized").first()
    assert group is not None, "Uncategorized category group is missing - has the migration been run?"
    return group.id


@pytest.fixture
def mock_email(monkeypatch):
    # Shared by any test that triggers jobs.send_password_reset_email
    # (currently test_background_jobs.py and test_auth_tokens.py) - patches
    # email_client's real Resend API call so tests never send a real email
    # over the network, same idea as test_payments.py's mock_pesapal
    # fixture for PesaPal. jobs.py does `import email_client` (the whole
    # module, not `from email_client import send_email`), so patching the
    # attribute on email_client itself is enough - jobs.py looks it up on
    # the same module object at call time, unlike routers/payments.py's
    # PesaPal functions which needed patching at their point of use instead.
    sent = []

    def fake_send_email(to_email, subject, body, html=None):
        sent.append({"to_email": to_email, "subject": subject, "body": body, "html": html})

    monkeypatch.setattr("email_client.is_configured", lambda: True)
    monkeypatch.setattr("email_client.send_email", fake_send_email)
    return sent


@pytest.fixture
def mock_pesapal(monkeypatch):
    # Shared (moved here from test_payments.py 2026-09-16) - now that every
    # order must be paid before it's placed, more than one test file needs
    # to drive a real checkout through to a confirmed PesaPal payment, not
    # just test_payments.py itself.
    #
    # Patched where it's USED (routers.payments), not where it's defined
    # (pesapal_client) - routers/payments.py already imported these names
    # directly, so patching pesapal_client itself wouldn't affect the
    # reference routers/payments.py is holding.
    #
    # status_responses lets each test control what "PesaPal" reports back
    # for a given order_tracking_id before hitting the webhook - defaults
    # to FAILED for anything a test didn't explicitly set.
    status_responses = {}

    def fake_submit_order_request(merchant_reference, amount, currency, description, billing_email, billing_phone, billing_first_name, billing_last_name, callback_url=None):
        return {
            "order_tracking_id": f"PESAPAL-{merchant_reference}",
            "redirect_url": f"https://cybqa.pesapal.com/pesapalv3/mock-checkout/{merchant_reference}",
        }

    def fake_get_transaction_status(order_tracking_id):
        return status_responses.get(order_tracking_id, {"payment_status_description": "FAILED"})

    # Also patched - there are no real PesaPal credentials in this dev/test
    # environment, so without this every test using this fixture would hit
    # PaymentsUnavailableError before ever reaching the fakes above.
    monkeypatch.setattr("routers.payments.is_configured", lambda: True)
    monkeypatch.setattr("routers.payments.submit_order_request", fake_submit_order_request)
    monkeypatch.setattr("routers.payments.get_transaction_status", fake_get_transaction_status)

    return status_responses


def pay_order(client, order_id, mock_pesapal, headers=None):
    # Drives a real order through to a CONFIRMED PesaPal payment, the same
    # two real calls a paying customer's frontend makes (POST .../payments,
    # then PesaPal's own webhook hitting us back) - added 2026-09-16
    # alongside "must be paid before it's placed": checkout() alone no
    # longer produces a placed order, so any test that needs one now has to
    # actually pay for it, not just check it out. headers=None means a
    # guest order - the caller is expected to have already put the right
    # X-Guest-Token in headers itself if so.
    response = client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"provider": "mobile_money"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    payment = unwrap(response)

    mock_pesapal[payment["provider_reference"]] = {"payment_status_description": "Completed"}
    response = client.get(
        "/api/v1/payments/webhook",
        params={"OrderTrackingId": payment["provider_reference"], "OrderMerchantReference": payment["id"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == 200

    return payment
