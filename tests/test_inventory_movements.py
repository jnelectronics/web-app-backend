# Covers the inventory_movements audit trail: written automatically by
# creating a stock record, adjusting one, checkout (SOLD), and cancel
# (restore) - plus the GET .../movements endpoint's role gating.

import uuid

import pytest

from conftest import unwrap
from models import (
    AuditLog,
    Branch,
    Cart,
    CartItem,
    Category,
    Customer,
    InventoryMovement,
    InventoryRecord,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from security import create_access_token, hash_password


@pytest.fixture
def staff_tokens(db):
    created = {}
    for role in StaffRole:
        staff = StaffUser(
            full_name=f"Movement Test {role.value}",
            email=f"{role.value}-mv-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("Password123"),
            role=role,
        )
        db.add(staff)
        created[role] = staff
    db.commit()

    tokens = {role: create_access_token(subject=str(s.id), account_type="staff") for role, s in created.items()}
    yield tokens

    # Adjusting inventory now also writes an audit_logs row
    # (routers/inventory.py calls write_audit_log) - must go before
    # deleting the staff it's attributed to, same FK-order reasoning as
    # everywhere else in this file.
    staff_ids = [s.id for s in created.values()]
    db.query(AuditLog).filter(AuditLog.staff_user_id.in_(staff_ids)).delete(synchronize_session=False)
    db.commit()
    for staff in created.values():
        db.delete(staff)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def variant_and_branch(db):
    category = Category(name=f"Mv Test Category {uuid.uuid4().hex[:8]}")
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Mv Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()
    branch = Branch(name="Mv Test Branch", address="Test Address")
    db.add(branch)
    db.commit()

    yield variant, branch

    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()
    db.query(Branch).filter(Branch.id == branch.id).delete()
    db.commit()


def test_creating_record_with_stock_logs_stock_in(client, db, variant_and_branch, staff_tokens):
    variant, branch = variant_and_branch
    headers = _auth(staff_tokens[StaffRole.INVENTORY_MANAGER])

    response = client.post(
        "/api/v1/inventory",
        json={"variant_id": str(variant.id), "branch_id": str(branch.id), "quantity_available": 20},
        headers=headers,
    )
    assert response.status_code == 200
    record_id = unwrap(response)["id"]

    response = client.get(f"/api/v1/inventory/{record_id}/movements", headers=headers)
    assert response.status_code == 200
    movements = unwrap(response)
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "stock_in"
    assert movements[0]["quantity_changed"] == 20

    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == uuid.UUID(record_id)).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == uuid.UUID(record_id)).delete()
    db.commit()


def test_adjust_logs_custom_movement_type_and_reason(client, db, variant_and_branch, staff_tokens):
    variant, branch = variant_and_branch
    record = InventoryRecord(variant_id=variant.id, branch_id=branch.id, quantity_available=10)
    db.add(record)
    db.commit()
    headers = _auth(staff_tokens[StaffRole.INVENTORY_MANAGER])

    response = client.patch(
        f"/api/v1/inventory/{record.id}/adjust",
        json={"quantity_change": -3, "movement_type": "stock_out", "reason": "Damaged in storage"},
        headers=headers,
    )
    assert response.status_code == 200

    response = client.get(f"/api/v1/inventory/{record.id}/movements", headers=headers)
    movements = unwrap(response)
    assert movements[0]["movement_type"] == "stock_out"
    assert movements[0]["reason"] == "Damaged in storage"

    # System Administrator isn't allowed to view movements either (same
    # role set as viewing inventory itself)
    response = client.get(
        f"/api/v1/inventory/{record.id}/movements", headers=_auth(staff_tokens[StaffRole.SYSTEM_ADMINISTRATOR])
    )
    assert response.status_code == 403

    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()


def test_checkout_and_cancel_log_sold_and_restore_movements(client, db, variant_and_branch, staff_tokens):
    variant, branch = variant_and_branch
    record = InventoryRecord(variant_id=variant.id, branch_id=branch.id, quantity_available=10)
    db.add(record)

    customer = Customer(
        full_name="Movement Customer",
        email=f"mvcust-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add(customer)
    db.commit()

    customer_token = create_access_token(subject=str(customer.id), account_type="customer")

    add_response = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(variant.id), "quantity": 2},
        headers=_auth(customer_token),
    )
    assert add_response.status_code == 200

    checkout_response = client.post(
        "/api/v1/orders",
        json={
            "guest_full_name": "Movement Customer",
            "guest_phone_number": "+256700000000",
            "delivery_address": "Test Address",
        },
        headers=_auth(customer_token),
    )
    assert checkout_response.status_code == 200
    order = unwrap(checkout_response)

    staff_headers = _auth(staff_tokens[StaffRole.INVENTORY_MANAGER])
    response = client.get(f"/api/v1/inventory/{record.id}/movements", headers=staff_headers)
    movements = unwrap(response)
    assert any(m["movement_type"] == "sold" and m["quantity_changed"] == -2 for m in movements)
    assert all(m["staff_user_id"] is None for m in movements if m["movement_type"] == "sold")

    cancel_response = client.patch(f"/api/v1/orders/{order['id']}/cancel", headers=_auth(customer_token))
    assert cancel_response.status_code == 200

    response = client.get(f"/api/v1/inventory/{record.id}/movements", headers=staff_headers)
    movements = unwrap(response)
    assert any(
        m["movement_type"] == "adjustment" and m["quantity_changed"] == 2 for m in movements
    )

    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.order_id == uuid.UUID(order["id"])).delete()
    db.commit()
    db.query(Order).filter(Order.id == uuid.UUID(order["id"])).delete()
    db.commit()
    cart_ids = [c.id for c in db.query(Cart).filter(Cart.customer_id == customer.id).all()]
    db.query(CartItem).filter(CartItem.cart_id.in_(cart_ids)).delete(synchronize_session=False)
    db.commit()
    db.query(Cart).filter(Cart.id.in_(cart_ids)).delete(synchronize_session=False)
    db.commit()
    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
