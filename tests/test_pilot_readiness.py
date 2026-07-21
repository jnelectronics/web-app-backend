# Covers pilot-readiness pieces that aren't a "phase" of their own:
# CORS (so a browser-based frontend can actually call this API) and staff
# self-service password change (so the seeded admin account isn't stuck on
# its well-known default password forever).

import uuid

import pytest

from conftest import unwrap
from models import StaffRole, StaffUser
from security import create_access_token, hash_password


def test_cors_allows_configured_origin(client):
    response = client.get("/api/v1/categories", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_rejects_unknown_origin(client):
    response = client.get("/api/v1/categories", headers={"Origin": "https://not-our-frontend.example"})
    assert response.status_code == 200
    # No CORS header for a disallowed origin - the response body still
    # comes back (TestClient doesn't enforce CORS, a real browser would),
    # but nothing tells a real browser it's allowed to hand the response
    # to JavaScript.
    assert "access-control-allow-origin" not in response.headers


@pytest.fixture
def staff_member(db):
    staff = StaffUser(
        full_name="Password Test",
        email=f"pwtest-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("OriginalPass123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()

    yield staff

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_staff_can_change_own_password_even_as_sales_attendant(client, staff_member):
    # Sales Attendants are locked out of the REST of this module
    # (BR-USER-003) - this endpoint is the one deliberate exception.
    token = create_access_token(subject=str(staff_member.id), account_type="staff")

    response = client.patch(
        "/api/v1/staff/me/password",
        json={"current_password": "WrongPassword", "new_password": "NewPass456"},
        headers=_auth(token),
    )
    assert response.status_code == 401

    response = client.patch(
        "/api/v1/staff/me/password",
        json={"current_password": "OriginalPass123", "new_password": "NewPass456"},
        headers=_auth(token),
    )
    assert response.status_code == 200

    response = client.post(
        "/api/v1/auth/staff/login", json={"email": staff_member.email, "password": "NewPass456"}
    )
    assert response.status_code == 200

    response = client.post(
        "/api/v1/auth/staff/login", json={"email": staff_member.email, "password": "OriginalPass123"}
    )
    assert response.status_code == 401
