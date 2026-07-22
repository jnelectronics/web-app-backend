# Covers the two pieces built to close the last open pilot-readiness gap:
# password complexity rules (schemas.py's PasswordStr) and failed-login
# rate limiting (rate_limit.py), applied to both customer and staff login.

import uuid

import pytest

from conftest import unwrap
from models import Customer, StaffRole, StaffUser
from rate_limit import MAX_FAILED_ATTEMPTS, check_not_locked_out, clear_failed_attempts, record_failed_attempt
from security import create_access_token, hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- Password complexity ---


@pytest.mark.parametrize(
    "password",
    [
        "short1",  # under 8 characters
        "alllettersnodigits",  # no digit
        "12345678",  # no letter
    ],
)
def test_register_rejects_weak_passwords(client, password):
    response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Weak Password", "email": f"weak-{uuid.uuid4().hex[:8]}@example.com", "password": password},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_register_accepts_a_strong_password(client, db):
    email = f"strong-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Strong Password", "email": email, "password": "GoodPass123"},
    )
    assert response.status_code == 200

    customer_id = uuid.UUID(unwrap(response)["customer"]["id"])
    db.query(Customer).filter(Customer.id == customer_id).delete()
    db.commit()


@pytest.fixture
def inventory_manager_token(db):
    staff = StaffUser(
        full_name="Password Rule Manager",
        email=f"pwrule-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.INVENTORY_MANAGER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def test_staff_create_rejects_weak_password(client, inventory_manager_token):
    response = client.post(
        "/api/v1/staff",
        json={
            "full_name": "New Staff",
            "email": f"newstaff-{uuid.uuid4().hex[:8]}@example.com",
            "password": "weak",
            "role": "sales_attendant",
        },
        headers=_auth(inventory_manager_token),
    )
    assert response.status_code == 422


# --- Login rate limiting: unit-level (rate_limit.py directly) ---


def test_check_not_locked_out_raises_after_max_attempts():
    key = f"test:{uuid.uuid4().hex}"
    try:
        for _ in range(MAX_FAILED_ATTEMPTS):
            check_not_locked_out(key)  # not locked out yet on each of these
            record_failed_attempt(key)

        with pytest.raises(Exception) as exc_info:
            check_not_locked_out(key)
        assert exc_info.value.retry_after_seconds > 0
    finally:
        clear_failed_attempts(key)


def test_clear_failed_attempts_resets_the_lockout():
    key = f"test:{uuid.uuid4().hex}"
    try:
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_failed_attempt(key)
        clear_failed_attempts(key)
        # No exception - the slate is clean.
        check_not_locked_out(key)
    finally:
        clear_failed_attempts(key)


# --- Login rate limiting: through the real HTTP endpoints ---


@pytest.fixture
def customer_with_password(db):
    customer = Customer(
        full_name="Rate Limit Test",
        email=f"ratelimit-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("RealPassword123"),
    )
    db.add(customer)
    db.commit()

    yield customer

    clear_failed_attempts(f"customer:{customer.email}")
    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()


def test_login_locks_out_after_repeated_failures(client, customer_with_password):
    email = customer_with_password.email

    for _ in range(MAX_FAILED_ATTEMPTS):
        response = client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPassword1"})
        assert response.status_code == 401

    # Even the CORRECT password is now rejected - the account is locked,
    # not just "that one guess was wrong."
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "RealPassword123"})
    assert response.status_code == 429
    assert response.json()["error_code"] == "RATE_LIMITED"
    assert "Retry-After" in response.headers


def test_successful_login_resets_the_failure_count(client, customer_with_password):
    email = customer_with_password.email

    # A couple of failures, but under the limit.
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPassword1"})

    response = client.post("/api/v1/auth/login", json={"email": email, "password": "RealPassword123"})
    assert response.status_code == 200

    # If the counter had carried over, one more wrong guess would now
    # trip the lockout - it shouldn't, since success reset it to zero.
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPassword1"})
    assert response.status_code == 401
