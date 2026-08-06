# Covers the audit_logs read endpoint: its distinctive role pair
# (Inventory Manager + System Administrator, NOT Sales Attendant - the
# opposite of most staff-gated endpoints in this project), filtering, and
# that a real write (creating a product) actually produces an entry.

import uuid

import pytest

from conftest import unwrap
from models import AuditLog, Category, Product, StaffRole, StaffUser
from security import create_access_token, hash_password


@pytest.fixture
def staff_tokens(db):
    created = {}
    for role in StaffRole:
        staff = StaffUser(
            full_name=f"Audit Test {role.value}",
            email=f"{role.value}-audit-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("Password123"),
            role=role,
        )
        db.add(staff)
        created[role] = staff
    db.commit()

    tokens = {role: create_access_token(subject=str(s.id), account_type="staff") for role, s in created.items()}
    yield tokens, created

    for staff in created.values():
        db.delete(staff)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_audit_log_role_gating(client, staff_tokens):
    tokens, _ = staff_tokens

    # Sales Attendant is explicitly excluded here, unlike most other
    # staff-gated endpoints in this project.
    response = client.get("/api/v1/audit-logs", headers=_auth(tokens[StaffRole.SALES_ATTENDANT]))
    assert response.status_code == 403

    response = client.get("/api/v1/audit-logs", headers=_auth(tokens[StaffRole.INVENTORY_MANAGER]))
    assert response.status_code == 200

    response = client.get("/api/v1/audit-logs", headers=_auth(tokens[StaffRole.SYSTEM_ADMINISTRATOR]))
    assert response.status_code == 200


def test_creating_a_product_writes_an_audit_entry(client, db, staff_tokens):
    tokens, created = staff_tokens
    manager_token = tokens[StaffRole.INVENTORY_MANAGER]
    manager = created[StaffRole.INVENTORY_MANAGER]

    category = Category(name=f"Audit Test Category {uuid.uuid4().hex[:8]}")
    db.add(category)
    db.commit()

    try:
        response = client.post(
            "/api/v1/products",
            json={"category_id": str(category.id), "name": "Audited Product"},
            headers=_auth(manager_token),
        )
        assert response.status_code == 200
        product_id = unwrap(response)["id"]

        response = client.get(
            "/api/v1/audit-logs",
            params={"resource_type": "product", "resource_id": product_id},
            headers=_auth(manager_token),
        )
        assert response.status_code == 200
        entries = unwrap(response)["items"]
        assert len(entries) == 1
        assert entries[0]["action"] == "product.create"
        assert entries[0]["staff_user_id"] == str(manager.id)
        assert entries[0]["staff_full_name"] == manager.full_name
        assert entries[0]["staff_email"] == manager.email
        assert entries[0]["new_value"]["name"] == "Audited Product"
        assert entries[0]["previous_value"] is None

        # staff_user_id filter narrows to just this manager's entries
        response = client.get(
            "/api/v1/audit-logs",
            params={"staff_user_id": str(manager.id)},
            headers=_auth(manager_token),
        )
        assert response.status_code == 200
        assert len(unwrap(response)["items"]) >= 1

        # action filter
        response = client.get(
            "/api/v1/audit-logs",
            params={"action": "product.create", "resource_id": product_id},
            headers=_auth(manager_token),
        )
        assert response.status_code == 200
        assert len(unwrap(response)["items"]) == 1
    finally:
        db.query(AuditLog).filter(AuditLog.resource_type == "product").filter(
            AuditLog.staff_user_id == manager.id
        ).delete()
        db.commit()
        db.query(Product).filter(Product.category_id == category.id).delete()
        db.commit()
        db.query(Category).filter(Category.id == category.id).delete()
        db.commit()
