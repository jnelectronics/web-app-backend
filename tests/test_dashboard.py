# Covers the Admin Dashboard: role-gated endpoints. As of 2026-08-30 (client
# UAT request), Sales Attendant was removed from this module entirely - so
# every endpoint here is now Owner/System-Administrator-only, and the old
# FR-ADMIN-003 "can call it but revenue is hidden" nuance for Sales
# Attendant no longer applies (they get a plain 403 before reaching that
# logic at all).

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


def test_summary_rejects_sales_attendant_and_shows_revenue_to_owner(client, staff_tokens):
    response = client.get(
        "/api/v1/admin/dashboard/summary", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 403

    response = client.get(
        "/api/v1/admin/dashboard/summary", headers=_auth(staff_tokens[StaffRole.OWNER])
    )
    assert response.status_code == 200
    assert unwrap(response)["total_revenue"] is not None


def test_recent_orders_rejects_sales_attendant(client, staff_tokens):
    response = client.get(
        "/api/v1/admin/dashboard/recent-orders", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 403

    response = client.get(
        "/api/v1/admin/dashboard/recent-orders", headers=_auth(staff_tokens[StaffRole.OWNER])
    )
    assert response.status_code == 200


def test_low_inventory_and_sales_summary_reject_sales_attendant(client, staff_tokens):
    # 2026-08-30: Sales Attendant lost the whole Dashboard module, reversing
    # the 2026-08-18 widening for this one module specifically (everywhere
    # else that widening still stands).
    response = client.get(
        "/api/v1/admin/dashboard/low-inventory", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 403

    response = client.get(
        "/api/v1/admin/dashboard/low-inventory", headers=_auth(staff_tokens[StaffRole.OWNER])
    )
    assert response.status_code == 200

    response = client.get(
        "/api/v1/admin/dashboard/sales-summary", headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT])
    )
    assert response.status_code == 403

    response = client.get(
        "/api/v1/admin/dashboard/sales-summary", headers=_auth(staff_tokens[StaffRole.OWNER])
    )
    assert response.status_code == 200
    body = unwrap(response)
    assert "total_revenue" in body
    assert "average_order_value" in body
