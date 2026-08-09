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
