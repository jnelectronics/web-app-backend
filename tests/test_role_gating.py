# Verifies the Inventory Manager / Sales Attendant role gates just added to
# categories, products, variants, branches, and inventory - the "existing
# catalog/branches/inventory endpoints are wide open" gap called out in
# CLAUDE.md's "Current status" section. Only covers categories + branches +
# inventory directly (products/variants use the exact same
# require_staff_role(StaffRole.INVENTORY_MANAGER) pattern, already proven
# here) to avoid five near-identical copies of the same test.

import uuid

import pytest

from conftest import unwrap
from models import (
    AuditLog,
    Branch,
    Category,
    InventoryMovement,
    InventoryRecord,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


@pytest.fixture
def staff_tokens(db):
    # One StaffUser per role, so tests can prove both sides of a gate: the
    # right role gets through, every other role gets 403.
    created = {}
    for role in StaffRole:
        staff = StaffUser(
            full_name=f"Test {role.value}",
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("Password123"),
            role=role,
        )
        db.add(staff)
        created[role] = staff
    db.commit()

    tokens = {
        role: create_access_token(subject=str(staff.id), account_type="staff")
        for role, staff in created.items()
    }
    yield tokens

    # Adjusting inventory (test_inventory_adjust_requires_inventory_manager)
    # now also writes an audit_logs row - must go before deleting the staff
    # it's attributed to.
    staff_ids = [s.id for s in created.values()]
    db.query(AuditLog).filter(AuditLog.staff_user_id.in_(staff_ids)).delete(synchronize_session=False)
    db.commit()
    for staff in created.values():
        db.delete(staff)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_category_read_stays_public(client):
    response = client.get("/api/v1/categories")
    assert response.status_code == 200


def test_category_write_requires_inventory_manager(client, staff_tokens, db):
    # No token at all - HTTPBearer itself rejects this before our role
    # check ever runs.
    response = client.post("/api/v1/categories", json={"name": f"Cat {uuid.uuid4().hex[:8]}"})
    assert response.status_code in (401, 403)

    # Wrong role
    response = client.post(
        "/api/v1/categories",
        json={"name": f"Cat {uuid.uuid4().hex[:8]}"},
        headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT]),
    )
    assert response.status_code == 403

    # System Administrator isn't listed for this endpoint either - no
    # implicit admin superset, per API spec §3.3 ("every endpoint lists the
    # roles permitted to call it").
    response = client.post(
        "/api/v1/categories",
        json={"name": f"Cat {uuid.uuid4().hex[:8]}"},
        headers=_auth(staff_tokens[StaffRole.SYSTEM_ADMINISTRATOR]),
    )
    assert response.status_code == 403

    # Right role
    response = client.post(
        "/api/v1/categories",
        json={"name": f"Cat {uuid.uuid4().hex[:8]}"},
        headers=_auth(staff_tokens[StaffRole.INVENTORY_MANAGER]),
    )
    assert response.status_code == 200
    db.query(Category).filter(Category.id == uuid.UUID(unwrap(response)["id"])).delete()
    db.commit()


def test_branch_read_requires_any_staff_role(client, staff_tokens):
    response = client.get("/api/v1/branches")
    assert response.status_code in (401, 403)

    # ANY staff role can list branches - the docs say just "Staff" here,
    # not a specific role, unlike categories/products/inventory writes.
    for role in StaffRole:
        response = client.get("/api/v1/branches", headers=_auth(staff_tokens[role]))
        assert response.status_code == 200


def test_branch_write_requires_inventory_manager(client, staff_tokens, db):
    response = client.post(
        "/api/v1/branches",
        json={"name": "Test Branch", "address": "123 Test Street"},
        headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT]),
    )
    assert response.status_code == 403

    response = client.post(
        "/api/v1/branches",
        json={"name": "Test Branch", "address": "123 Test Street"},
        headers=_auth(staff_tokens[StaffRole.INVENTORY_MANAGER]),
    )
    assert response.status_code == 200
    db.query(Branch).filter(Branch.id == uuid.UUID(unwrap(response)["id"])).delete()
    db.commit()


@pytest.fixture
def inventory_record(db):
    category = Category(name=f"Inv Test Category {uuid.uuid4().hex[:8]}")
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Inv Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()
    branch = Branch(name="Inv Test Branch", address="Test Address")
    db.add(branch)
    db.flush()
    record = InventoryRecord(variant_id=variant.id, branch_id=branch.id, quantity_available=10)
    db.add(record)
    db.commit()

    yield record

    # Adjusting inventory (test_inventory_adjust_requires_inventory_manager)
    # now also writes an inventory_movements row referencing this record -
    # must go first, or deleting the record below hits a foreign key
    # violation.
    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()
    db.query(Branch).filter(Branch.id == branch.id).delete()
    db.commit()


def test_inventory_view_allows_sales_attendant_and_inventory_manager(
    client, staff_tokens, inventory_record
):
    for role in (StaffRole.SALES_ATTENDANT, StaffRole.INVENTORY_MANAGER):
        response = client.get(
            f"/api/v1/inventory/{inventory_record.id}", headers=_auth(staff_tokens[role])
        )
        assert response.status_code == 200

    # System Administrator is NOT listed for inventory viewing per the docs
    response = client.get(
        f"/api/v1/inventory/{inventory_record.id}",
        headers=_auth(staff_tokens[StaffRole.SYSTEM_ADMINISTRATOR]),
    )
    assert response.status_code == 403


def test_inventory_adjust_requires_inventory_manager(client, staff_tokens, inventory_record):
    response = client.patch(
        f"/api/v1/inventory/{inventory_record.id}/adjust",
        json={"quantity_change": 1},
        headers=_auth(staff_tokens[StaffRole.SALES_ATTENDANT]),
    )
    assert response.status_code == 403

    response = client.patch(
        f"/api/v1/inventory/{inventory_record.id}/adjust",
        json={"quantity_change": 1},
        headers=_auth(staff_tokens[StaffRole.INVENTORY_MANAGER]),
    )
    assert response.status_code == 200
