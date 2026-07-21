# Covers the full token lifecycle added on top of register/login:
# /auth/refresh, /auth/logout (revoke-all), and the
# /auth/password/forgot + /auth/password/reset pair.

import uuid

from conftest import unwrap
from models import Customer, RefreshToken


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


def test_password_forgot_and_reset(client, db):
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

        response = client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert response.status_code == 200
        reset_token = unwrap(response)["reset_token"]

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
