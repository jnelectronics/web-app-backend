# Covers the async job pattern actually wired into the app right now:
# /auth/password/forgot hands the "send an email" job to FastAPI's
# BackgroundTasks, which runs it in this same process after the response
# is sent - not the RQ+Redis+worker.py setup (still in the codebase, kept
# for a possible future switch, but not called from routers/auth.py
# anymore - see CLAUDE.md's "Background workers" section for why).
#
# email_client's real Gmail SMTP call is mocked here (see mock_email below)
# - same idea as test_payments.py's mock_pesapal fixture for PesaPal. This
# suite verifies OUR logic (the job runs, builds the right link, logs the
# right outcome), not that Gmail itself works - that got verified once,
# separately, by actually sending a real email to a real inbox (see
# CLAUDE.md's Pilot readiness section), not on every pytest run, which
# shouldn't depend on network access or real credentials.

import uuid

import pytest

from conftest import unwrap
from models import Customer, RefreshToken


@pytest.fixture
def mock_email(monkeypatch):
    # jobs.py does `import email_client` (the whole module, not
    # `from email_client import send_email`), so patching the attribute on
    # email_client itself is enough - jobs.py looks it up on the same
    # module object at call time, unlike routers/payments.py's PesaPal
    # functions which needed patching at their point of use instead (see
    # mock_pesapal's comment in test_payments.py for that distinction).
    sent = []

    def fake_send_email(to_email, subject, body):
        sent.append({"to_email": to_email, "subject": subject, "body": body})

    monkeypatch.setattr("email_client.is_configured", lambda: True)
    monkeypatch.setattr("email_client.send_email", fake_send_email)
    return sent


def test_password_forgot_runs_the_email_job(client, db, caplog, mock_email):
    email = f"jobtest-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Job Test", "email": email, "password": "Password123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        response = client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert response.status_code == 200

        # TestClient runs the ASGI app through to completion - including
        # any BackgroundTasks - before client.post() returns, so the job
        # has already run by this point (unlike a real deployed server,
        # where it runs after the response reaches the client but the
        # process is still the one that handles it either way).
        assert len(mock_email) == 1
        assert mock_email[0]["to_email"] == email
        reset_token = unwrap(response)["reset_token"]
        assert reset_token in mock_email[0]["body"]
        assert f"Password reset email sent to {email}" in caplog.text
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()
