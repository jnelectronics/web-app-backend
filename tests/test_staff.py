# No dedicated staff test file existed before - covers the gaps found
# during frontend integration: GET /staff/me, GET /staff/{id}, and the
# admin-initiated password reset (none of these existed at all before).

import uuid

import pytest

from conftest import unwrap
from models import AuditLog, StaffRole, StaffUser
from security import create_access_token, hash_password, verify_password


@pytest.fixture
def manager(db):
    staff = StaffUser(
        full_name="Staff Test Manager",
        email=f"staffmgr-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()
    yield staff
    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


@pytest.fixture
def sales_attendant(db):
    staff = StaffUser(
        full_name="Staff Test Attendant",
        email=f"staffatt-{uuid.uuid4().hex[:8]}@example.com",
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


def test_read_my_staff_profile_works_for_any_role(client, sales_attendant):
    token = create_access_token(subject=str(sales_attendant.id), account_type="staff")
    response = client.get("/api/v1/staff/me", headers=_auth(token))
    assert response.status_code == 200
    body = unwrap(response)
    assert body["id"] == str(sales_attendant.id)
    assert body["role"] == "sales_attendant"


def test_read_single_staff_requires_manage_role(client, manager, sales_attendant):
    manager_token = create_access_token(subject=str(manager.id), account_type="staff")
    attendant_token = create_access_token(subject=str(sales_attendant.id), account_type="staff")

    response = client.get(f"/api/v1/staff/{sales_attendant.id}", headers=_auth(manager_token))
    assert response.status_code == 200
    assert unwrap(response)["id"] == str(sales_attendant.id)

    # A Sales Attendant is locked out of the whole module, same as every
    # other /staff endpoint except /me and /me/password.
    response = client.get(f"/api/v1/staff/{sales_attendant.id}", headers=_auth(attendant_token))
    assert response.status_code == 403


def test_admin_reset_staff_password(client, db, manager, sales_attendant):
    manager_token = create_access_token(subject=str(manager.id), account_type="staff")

    response = client.post(
        f"/api/v1/staff/{sales_attendant.id}/reset-password", headers=_auth(manager_token)
    )
    assert response.status_code == 200
    temporary_password = unwrap(response)["temporary_password"]
    assert len(temporary_password) > 8

    db.refresh(sales_attendant)
    assert verify_password(temporary_password, sales_attendant.password_hash)

    # The new temporary password actually works for a real login
    response = client.post(
        "/api/v1/auth/staff/login",
        json={"email": sales_attendant.email, "password": temporary_password},
    )
    assert response.status_code == 200

    # reset_staff_password writes an audit_logs row referencing BOTH staff
    # members (actor + resource) - has to be cleaned up here, before this
    # test's own manager/sales_attendant fixtures tear down and try to
    # delete those StaffUser rows (see CLAUDE.md's LIFO fixture-teardown
    # gotcha - the fixtures' own teardown can't defend against a reference
    # THIS test created).
    db.query(AuditLog).filter(AuditLog.resource_id == sales_attendant.id).delete()
    db.commit()


def test_update_my_staff_profile_only_touches_name_and_phone(client, sales_attendant):
    token = create_access_token(subject=str(sales_attendant.id), account_type="staff")

    response = client.patch(
        "/api/v1/staff/me",
        json={"full_name": "New Name", "phone_number": "+256700000099"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert body["full_name"] == "New Name"
    assert body["phone_number"] == "+256700000099"
    # Untouched - role/email aren't even accepted fields on this schema.
    assert body["role"] == "sales_attendant"

    # phone_number can be explicitly cleared back to null.
    response = client.patch("/api/v1/staff/me", json={"phone_number": None}, headers=_auth(token))
    assert response.status_code == 200
    assert unwrap(response)["phone_number"] is None

    # role/email are hard-rejected (422), not silently ignored -
    # StaffProfileUpdate's extra="forbid" makes an unknown field a
    # validation error instead of a no-op, per the frontend handoff doc.
    response = client.patch(
        "/api/v1/staff/me",
        json={"full_name": "Still Me", "role": "system_administrator", "email": "hijacked@example.com"},
        headers=_auth(token),
    )
    assert response.status_code == 422

    # And the attempted escalation really did nothing - full_name from
    # that rejected request never got applied either.
    me_response = client.get("/api/v1/staff/me", headers=_auth(token))
    body = unwrap(me_response)
    assert body["role"] == "sales_attendant"
    assert body["email"] != "hijacked@example.com"
    assert body["full_name"] == "New Name"


def test_new_staff_must_change_password_until_they_set_their_own(client, db, manager, mock_email):
    manager_token = create_access_token(subject=str(manager.id), account_type="staff")
    email = f"newhire-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/staff",
        json={
            "full_name": "New Hire",
            "email": email,
            "password": "TempPass123",
            "role": "sales_attendant",
        },
        headers=_auth(manager_token),
    )
    assert response.status_code == 200
    new_staff = unwrap(response)
    assert new_staff["must_change_password"] is True

    # A real (mocked) welcome email went out with the temporary password.
    assert len(mock_email) == 1
    assert mock_email[0]["to_email"] == email
    assert "TempPass123" in mock_email[0]["body"]

    login_response = client.post(
        "/api/v1/auth/staff/login", json={"email": email, "password": "TempPass123"}
    )
    assert login_response.status_code == 200
    token = unwrap(login_response)["access_token"]

    me_response = client.get("/api/v1/staff/me", headers=_auth(token))
    assert unwrap(me_response)["must_change_password"] is True

    # Setting a real password clears the flag.
    change_response = client.patch(
        "/api/v1/staff/me/password",
        json={"current_password": "TempPass123", "new_password": "RealPassword123"},
        headers=_auth(token),
    )
    assert change_response.status_code == 200

    me_response = client.get("/api/v1/staff/me", headers=_auth(token))
    assert unwrap(me_response)["must_change_password"] is False

    db.query(AuditLog).filter(AuditLog.resource_id == uuid.UUID(new_staff["id"])).delete()
    db.commit()
    db.query(StaffUser).filter(StaffUser.id == uuid.UUID(new_staff["id"])).delete()
    db.commit()


def test_reset_staff_password_sets_must_change_password(client, db, manager, sales_attendant):
    manager_token = create_access_token(subject=str(manager.id), account_type="staff")

    # sales_attendant fixture starts with must_change_password=False
    # (created directly in the DB, not through POST /staff).
    assert sales_attendant.must_change_password is False

    response = client.post(
        f"/api/v1/staff/{sales_attendant.id}/reset-password", headers=_auth(manager_token)
    )
    assert response.status_code == 200

    db.refresh(sales_attendant)
    assert sales_attendant.must_change_password is True

    db.query(AuditLog).filter(AuditLog.resource_id == sales_attendant.id).delete()
    db.commit()
