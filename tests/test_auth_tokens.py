# Covers the full token lifecycle added on top of register/login:
# /auth/refresh, /auth/logout (revoke-all), the
# /auth/password/forgot + /auth/password/reset pair, and Google sign-in.

import uuid

import pytest

from conftest import unwrap
from google_auth_client import GoogleTokenError
from jobs import PASSWORD_RESET_URL
from models import Customer, CustomerStatus, RefreshToken

# mock_email is defined in conftest.py - /auth/password/forgot no longer
# echoes the reset token back in its response (see routers/auth.py), so
# this suite has to pull it out of the (mocked) email instead, the same
# way a real customer would have to actually check their inbox.


@pytest.fixture
def mock_google_auth(monkeypatch):
    # Patched where it's USED (routers.auth), not where it's defined
    # (google_auth_client) - same gotcha as test_payments.py's mock_pesapal
    # and test_catalog_extensions.py's mock_cloudinary: routers/auth.py
    # already imported these names directly, so patching google_auth_client
    # itself wouldn't affect the reference routers/auth.py is holding.
    #
    # google_tokens maps a fake id_token string to the claims "Google"
    # (the fake) reports back for it - lets each test control exactly what
    # a given token verifies to, without a single real network call to
    # Google.
    google_tokens = {}

    def fake_verify_id_token(id_token):
        if id_token not in google_tokens:
            raise GoogleTokenError("Invalid Google ID token (test)")
        return google_tokens[id_token]

    monkeypatch.setattr("routers.auth.google_is_configured", lambda: True)
    monkeypatch.setattr("routers.auth.verify_google_id_token", fake_verify_id_token)
    return google_tokens


def test_register_issues_both_tokens(client, db):
    email = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "New User", "email": email, "password": "Password123"},
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert body["customer"]["email"] == email
    assert body["access_token"]
    assert body["refresh_token"]

    customer_id = uuid.UUID(body["customer"]["id"])
    db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id == customer_id).delete()
    db.commit()


def test_refresh_and_logout(client, db):
    email = f"refreshtest-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Refresh Test", "email": email, "password": "Password123"},
    )
    body = unwrap(register_response)
    customer_id = uuid.UUID(body["customer"]["id"])
    refresh_token = body["refresh_token"]

    try:
        # A valid refresh token yields a fresh access token
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert response.status_code == 200
        assert unwrap(response)["access_token"]

        # A garbage refresh token is rejected
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert response.status_code == 401

        # Logout revokes it - the SAME refresh token no longer works
        access_token = unwrap(register_response)["access_token"]
        response = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert response.status_code == 401
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()


def test_google_sign_in_creates_new_customer(client, db, mock_google_auth):
    email = f"googlenew-{uuid.uuid4().hex[:8]}@example.com"
    mock_google_auth["fake-token"] = {"email": email, "email_verified": True, "name": "Google User"}

    response = client.post("/api/v1/auth/google", json={"id_token": "fake-token"})
    assert response.status_code == 200
    body = unwrap(response)
    assert body["access_token"]
    assert body["refresh_token"]

    customer = db.query(Customer).filter(Customer.email == email).first()
    assert customer is not None
    # No password was ever set - this account can only sign in via Google,
    # same idea as a guest row having no password.
    assert customer.password_hash is None
    assert customer.full_name == "Google User"

    db.query(RefreshToken).filter(RefreshToken.owner_id == customer.id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()


def test_google_sign_in_links_existing_password_account(client, db, mock_google_auth):
    email = f"googlelink-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Existing Password User", "email": email, "password": "Password123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        mock_google_auth["fake-token"] = {
            "email": email,
            "email_verified": True,
            "name": "Existing Password User",
        }
        response = client.post("/api/v1/auth/google", json={"id_token": "fake-token"})
        assert response.status_code == 200
        assert unwrap(response)["access_token"]

        # Still exactly ONE Customer row for this email - linked to the
        # existing password account, not a second row created alongside it.
        assert db.query(Customer).filter(Customer.email == email).count() == 1
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()


def test_google_sign_in_rejects_unverified_email(client, mock_google_auth):
    email = f"googleunverified-{uuid.uuid4().hex[:8]}@example.com"
    # email_verified=False - Google itself isn't vouching this person
    # actually owns this address, so it must be rejected even though the
    # "signature" (the mock) is otherwise valid.
    mock_google_auth["fake-token"] = {"email": email, "email_verified": False, "name": "Unverified"}

    response = client.post("/api/v1/auth/google", json={"id_token": "fake-token"})
    assert response.status_code == 401


def test_google_sign_in_rejects_invalid_token(client, mock_google_auth):
    # "fake-token" was never registered in mock_google_auth - fake_verify_id_token
    # raises GoogleTokenError for it, same as a real forged/expired token would.
    response = client.post("/api/v1/auth/google", json={"id_token": "not-a-real-token"})
    assert response.status_code == 401


def test_google_sign_in_rejects_deactivated_account(client, db, mock_google_auth):
    email = f"googledeactivated-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Deactivated User", "email": email, "password": "Password123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        customer = db.get(Customer, customer_id)
        customer.status = CustomerStatus.INACTIVE
        db.commit()

        mock_google_auth["fake-token"] = {"email": email, "email_verified": True, "name": "Deactivated User"}
        response = client.post("/api/v1/auth/google", json={"id_token": "fake-token"})
        assert response.status_code == 403
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()


def test_google_sign_in_returns_503_when_not_configured(client, monkeypatch):
    # The genuine, un-mocked is_configured() check - no mock_google_auth
    # fixture here, so this exercises the real 503 degrade-gracefully path
    # (see GoogleSignInUnavailableError, routers/auth.py).
    monkeypatch.setattr("routers.auth.google_is_configured", lambda: False)
    response = client.post("/api/v1/auth/google", json={"id_token": "whatever"})
    assert response.status_code == 503


def test_password_forgot_and_reset(client, db, mock_email):
    email = f"resettest-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Reset Test", "email": email, "password": "OldPassword123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        # Unknown email - generic response, no token leaked, still 200
        response = client.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.com"})
        assert response.status_code == 200
        assert "reset_token" not in unwrap(response)
        assert len(mock_email) == 0  # no customer found - no email job queued at all

        response = client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert response.status_code == 200
        assert "reset_token" not in unwrap(response)

        # The token only ever reaches the customer through the (mocked)
        # email now - pull it out of the link the same way a real reset
        # link would be built: PASSWORD_RESET_URL + "?token=" + the token.
        assert len(mock_email) == 1
        link_prefix = f"{PASSWORD_RESET_URL}?token="
        body = mock_email[0]["body"]
        reset_token = body[body.index(link_prefix) + len(link_prefix) :].split("\n")[0].strip()

        # A bogus reset token is rejected
        response = client.post(
            "/api/v1/auth/password/reset", json={"token": "not-a-real-token", "new_password": "Whatever123"}
        )
        assert response.status_code == 401

        response = client.post(
            "/api/v1/auth/password/reset", json={"token": reset_token, "new_password": "NewPassword456"}
        )
        assert response.status_code == 200

        # New password works, old one doesn't
        response = client.post("/api/v1/auth/login", json={"email": email, "password": "NewPassword456"})
        assert response.status_code == 200
        response = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPassword123"})
        assert response.status_code == 401
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()
