# Covers the Admin Dashboard: role-gated endpoints, plus FR-ADMIN-003's
# subtler rule that /summary hides the revenue figure from a Sales
# Attendant even though they're allowed to call the endpoint at all.

import uuid

import pytest

from conftest import unwrap
from models import StaffRole, StaffUser
from security import create_access_token, hash_password


@pytest.fixture
def staff_tokens(db):
    created = {}
    for role in StaffRole:
        staff = StaffUser(
            full_name=f"Dashboard Test {role.value}",
            email=f"{role.value}-dash-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("Password123"),
            role=role,
        )
        db.add(staff)
        created[role] = staff
    db.commit()

    tokens = {role: create_access_token(subject=str(s.id), account_type="staff") for role, s in created.items()}
    yield tokens

    for staff in created.values():
        db.delete(staff)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_summary_hides_revenue_from_sales_attendant(client, staff_tokens):
    response = client.get(
        "/api/v1/admin/dashboard/summary", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert body["total_revenue"] is None
    assert "total_orders" in body

    response = client.get(
        "/api/v1/admin/dashboard/summary", headers=_auth(staff_tokens[StaffRole.OWNER])
    )
    assert response.status_code == 200
    assert unwrap(response)["total_revenue"] is not None


def test_recent_orders_open_to_both_roles(client, staff_tokens):
    for role in (StaffRole.SALES_ATTENDANT, StaffRole.OWNER):
        response = client.get("/api/v1/admin/dashboard/recent-orders", headers=_auth(staff_tokens[role]))
        assert response.status_code == 200


def test_low_inventory_and_sales_summary_allow_any_staff_role(client, staff_tokens):
    # As of the 2026-08-18 RBAC widening, Sales Attendant gained access to
    # every admin section except Staff and Audit Logs - these two
    # endpoints are open to all three staff roles now.
    for role in StaffRole:
        response = client.get(
            "/api/v1/admin/dashboard/low-inventory", headers=_auth(staff_tokens[role])
        )
        assert response.status_code == 200

    for role in StaffRole:
        response = client.get(
            "/api/v1/admin/dashboard/sales-summary", headers=_auth(staff_tokens[role])
        )
        assert response.status_code == 200
        body = unwrap(response)
        assert "total_revenue" in body
        assert "average_order_value" in body
