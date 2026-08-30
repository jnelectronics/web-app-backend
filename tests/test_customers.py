# Covers the /customers resource: self-service profile/password/order
# history for a registered customer, and the staff-facing directory
# (list/view/deactivate) gated to Inventory Manager.

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import Category, Customer, CustomerAddress, Order, Product, ProductVariant, RefreshToken, StaffRole, StaffUser
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
def owner_token(db):
    staff = StaffUser(
        full_name="Manager",
        email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


@pytest.fixture
def sales_attendant_token(db):
    staff = StaffUser(
        full_name="Attendant",
        email=f"attendant-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
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
    category = Category(name=f"Cust Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Cust Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()

    order = Order(
        order_number=f"JN-TEST-{uuid.uuid4().hex[:8]}",
        customer_id=customer.id,
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


def test_read_my_orders(client, customer, customer_order):
    token = create_access_token(subject=str(customer.id), account_type="customer")
    response = client.get("/api/v1/customers/me/orders", headers=_auth(token))
    assert response.status_code == 200
    orders = unwrap(response)
    assert len(orders) == 1
    assert orders[0]["id"] == str(customer_order.id)


def test_staff_directory_requires_owner(client, customer, owner_token):
    # No token - always 401, same as a present-but-invalid token (see
    # security.py's bearer_scheme comment for why that consistency matters).
    response = client.get("/api/v1/customers")
    assert response.status_code == 401

    response = client.get("/api/v1/customers", headers=_auth(owner_token))
    assert response.status_code == 200

    response = client.get(f"/api/v1/customers/{customer.id}", headers=_auth(owner_token))
    assert response.status_code == 200
    assert unwrap(response)["id"] == str(customer.id)


def test_set_customer_status_blocks_login(client, customer, owner_token):
    customer_token_headers = _auth(owner_token)

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


def test_deactivate_customer_rejects_sales_attendant(client, customer, sales_attendant_token):
    # 2026-08-30, client UAT request: "Remove the Deactivate Customer option
    # from Sales Attendant accounts" - they keep read access to the
    # directory (list_customers/read_customer above), this is only the
    # write endpoint.
    response = client.patch(
        f"/api/v1/customers/{customer.id}/status",
        json={"status": "inactive"},
        headers=_auth(sales_attendant_token),
    )
    assert response.status_code == 403


def test_self_service_deactivate_revokes_refresh_tokens_and_rejects_reactivate(client, db, customer):
    login_response = client.post(
        "/api/v1/auth/login", json={"identifier": customer.email, "password": "OriginalPass123"}
    )
    assert login_response.status_code == 200
    tokens = unwrap(login_response)
    headers = _auth(tokens["access_token"])

    try:
        # A customer can only ever move THEIR OWN account to inactive
        # through this endpoint - never back to active.
        response = client.patch("/api/v1/customers/me/status", json={"status": "active"}, headers=headers)
        assert response.status_code == 403

        response = client.patch("/api/v1/customers/me/status", json={"status": "inactive"}, headers=headers)
        assert response.status_code == 200
        assert unwrap(response)["status"] == "inactive"

        # The refresh token issued at login is revoked as part of closing
        # the account - it no longer works for /auth/refresh.
        refresh_response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refresh_response.status_code == 401

        # Closing an already-inactive account is a clear 409, not a silent
        # no-op - unlike the staff-only endpoint above, which IS a safe
        # no-op on a repeat of the same value.
        response = client.patch("/api/v1/customers/me/status", json={"status": "inactive"}, headers=headers)
        assert response.status_code == 409
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer.id).delete()
        db.commit()


@pytest.fixture
def other_customer(db):
    other = Customer(
        full_name="Other Customer",
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("OtherPass123"),
    )
    db.add(other)
    db.commit()
    yield other
    db.query(Customer).filter(Customer.id == other.id).delete()
    db.commit()


def test_address_book_crud_and_default_swap(client, db, customer):
    token = create_access_token(subject=str(customer.id), account_type="customer")
    headers = _auth(token)

    try:
        response = client.get("/api/v1/customers/me/addresses", headers=headers)
        assert response.status_code == 200
        assert unwrap(response) == []

        response = client.post(
            "/api/v1/customers/me/addresses",
            json={
                "label": "Home",
                "recipient_name": "Jane Okello",
                "phone_number": "+256700000000",
                "address_line": "Plot 12, Kampala Road, Kampala",
                "is_default": True,
            },
            headers=headers,
        )
        assert response.status_code == 200
        home = unwrap(response)
        assert home["is_default"] is True

        # A second address, NOT marked default, shouldn't disturb the first.
        response = client.post(
            "/api/v1/customers/me/addresses",
            json={
                "label": "Office",
                "recipient_name": "Jane Okello",
                "phone_number": "+256700000001",
                "address_line": "Acacia Mall, Kampala",
            },
            headers=headers,
        )
        assert response.status_code == 200
        office = unwrap(response)
        assert office["is_default"] is False

        # Making Office the new default un-defaults Home.
        response = client.patch(
            f"/api/v1/customers/me/addresses/{office['id']}", json={"is_default": True}, headers=headers
        )
        assert response.status_code == 200
        assert unwrap(response)["is_default"] is True

        response = client.get("/api/v1/customers/me/addresses", headers=headers)
        by_id = {a["id"]: a for a in unwrap(response)}
        assert by_id[office["id"]]["is_default"] is True
        assert by_id[home["id"]]["is_default"] is False

        # A third address is fine...
        response = client.post(
            "/api/v1/customers/me/addresses",
            json={
                "label": "Mom's place",
                "recipient_name": "Jane Okello",
                "phone_number": "+256700000002",
                "address_line": "Ntinda, Kampala",
            },
            headers=headers,
        )
        assert response.status_code == 200

        # ...but a fourth hits the cap.
        response = client.post(
            "/api/v1/customers/me/addresses",
            json={
                "label": "Too Many",
                "recipient_name": "Jane Okello",
                "phone_number": "+256700000003",
                "address_line": "Somewhere Else",
            },
            headers=headers,
        )
        assert response.status_code == 409

        response = client.delete(f"/api/v1/customers/me/addresses/{home['id']}", headers=headers)
        assert response.status_code == 204

        response = client.get("/api/v1/customers/me/addresses", headers=headers)
        assert len(unwrap(response)) == 2
    finally:
        db.query(CustomerAddress).filter(CustomerAddress.customer_id == customer.id).delete()
        db.commit()


def test_address_book_is_isolated_per_customer(client, db, customer, other_customer):
    token = create_access_token(subject=str(customer.id), account_type="customer")
    other_token = create_access_token(subject=str(other_customer.id), account_type="customer")

    response = client.post(
        "/api/v1/customers/me/addresses",
        json={
            "label": "Home",
            "recipient_name": "Jane Okello",
            "phone_number": "+256700000000",
            "address_line": "Plot 12, Kampala Road, Kampala",
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    address_id = unwrap(response)["id"]

    try:
        # The other customer can neither see nor modify/delete it.
        response = client.get("/api/v1/customers/me/addresses", headers=_auth(other_token))
        assert unwrap(response) == []

        response = client.patch(
            f"/api/v1/customers/me/addresses/{address_id}", json={"label": "Hijacked"}, headers=_auth(other_token)
        )
        assert response.status_code == 404

        response = client.delete(f"/api/v1/customers/me/addresses/{address_id}", headers=_auth(other_token))
        assert response.status_code == 404
    finally:
        db.query(CustomerAddress).filter(CustomerAddress.customer_id == customer.id).delete()
        db.commit()
