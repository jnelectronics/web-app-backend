# Covers the /customers resource: self-service profile/password/order
# history for a registered customer, and the staff-facing directory
# (list/view/deactivate) gated to Inventory Manager.

import uuid

import pytest

from conftest import unwrap
from models import Branch, Category, Customer, Order, Product, ProductVariant, StaffRole, StaffUser
from security import create_access_token, hash_password


@pytest.fixture
def customer(db):
    customer = Customer(
        full_name="Original Name",
        email=f"customer-{uuid.uuid4().hex[:8]}@example.com",
        phone_number=f"+2567{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("OriginalPass123"),
    )
    db.add(customer)
    db.commit()

    yield customer

    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()


@pytest.fixture
def inventory_manager_token(db):
    staff = StaffUser(
        full_name="Manager",
        email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.INVENTORY_MANAGER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_read_and_update_my_profile(client, customer):
    token = create_access_token(subject=str(customer.id), account_type="customer")

    response = client.get("/api/v1/customers/me", headers=_auth(token))
    assert response.status_code == 200
    assert unwrap(response)["full_name"] == "Original Name"

    response = client.patch(
        "/api/v1/customers/me", json={"full_name": "Updated Name"}, headers=_auth(token)
    )
    assert response.status_code == 200
    assert unwrap(response)["full_name"] == "Updated Name"


def test_change_my_password(client, customer):
    token = create_access_token(subject=str(customer.id), account_type="customer")

    # Wrong current password is rejected
    response = client.patch(
        "/api/v1/customers/me/password",
        json={"current_password": "WrongPassword", "new_password": "NewPass456"},
        headers=_auth(token),
    )
    assert response.status_code == 401

    response = client.patch(
        "/api/v1/customers/me/password",
        json={"current_password": "OriginalPass123", "new_password": "NewPass456"},
        headers=_auth(token),
    )
    assert response.status_code == 200

    # The new password now works via the real login endpoint
    response = client.post(
        "/api/v1/auth/login", json={"identifier": customer.email, "password": "NewPass456"}
    )
    assert response.status_code == 200

    # The old password no longer does
    response = client.post(
        "/api/v1/auth/login", json={"identifier": customer.email, "password": "OriginalPass123"}
    )
    assert response.status_code == 401


@pytest.fixture
def customer_order(db, customer):
    category = Category(name=f"Cust Test Category {uuid.uuid4().hex[:8]}")
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Cust Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()
    branch = Branch(name="Cust Test Branch", address="Test Address")
    db.add(branch)
    db.flush()

    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=customer.id,
        fulfilling_branch_id=branch.id,
        guest_full_name=customer.full_name,
        guest_phone_number="+256700000000",
        delivery_address="Test Address",
        district="Test District",
        subtotal=1000.0,
        total=1000.0,
    )
    db.add(order)
    db.commit()

    yield order

    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()
    db.query(Branch).filter(Branch.id == branch.id).delete()
    db.commit()


def test_read_my_orders(client, customer, customer_order):
    token = create_access_token(subject=str(customer.id), account_type="customer")
    response = client.get("/api/v1/customers/me/orders", headers=_auth(token))
    assert response.status_code == 200
    orders = unwrap(response)
    assert len(orders) == 1
    assert orders[0]["id"] == str(customer_order.id)


def test_staff_directory_requires_inventory_manager(client, customer, inventory_manager_token):
    # No token
    response = client.get("/api/v1/customers")
    assert response.status_code in (401, 403)

    response = client.get("/api/v1/customers", headers=_auth(inventory_manager_token))
    assert response.status_code == 200

    response = client.get(f"/api/v1/customers/{customer.id}", headers=_auth(inventory_manager_token))
    assert response.status_code == 200
    assert unwrap(response)["id"] == str(customer.id)


def test_set_customer_status_blocks_login(client, customer, inventory_manager_token):
    customer_token_headers = _auth(inventory_manager_token)

    response = client.patch(
        f"/api/v1/customers/{customer.id}/status", json={"status": "inactive"}, headers=customer_token_headers
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "inactive"

    # Setting the SAME value again is a safe no-op, not an error and not a
    # silent flip back to active - the exact case a blind toggle got wrong.
    response = client.patch(
        f"/api/v1/customers/{customer.id}/status", json={"status": "inactive"}, headers=customer_token_headers
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "inactive"

    # A deactivated customer can no longer log in
    response = client.post(
        "/api/v1/auth/login", json={"identifier": customer.email, "password": "OriginalPass123"}
    )
    assert response.status_code == 403

    # Setting it back to active reactivates
    response = client.patch(
        f"/api/v1/customers/{customer.id}/status", json={"status": "active"}, headers=customer_token_headers
    )
    assert response.status_code == 200
    assert unwrap(response)["status"] == "active"

    response = client.post(
        "/api/v1/auth/login", json={"identifier": customer.email, "password": "OriginalPass123"}
    )
    assert response.status_code == 200
