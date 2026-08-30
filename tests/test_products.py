# Covers POST /products' collapsed create flow (added 2026-08-20, see
# CLAUDE.md's note): one call creates the Product AND its one default
# ProductVariant + InventoryRecord together, instead of needing a separate
# POST /variants call afterward. Nothing tested product creation through
# the real API at all before this - every other suite builds Product rows
# directly via the ORM in its own fixtures, bypassing this route entirely.

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import uncategorized_group_id, unwrap
from models import (
    AuditLog,
    Category,
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


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_token(db):
    staff = StaffUser(
        full_name="Product Test Owner",
        email=f"prodowner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    token = create_access_token(subject=str(staff.id), account_type="staff")

    yield token

    # product.create's audit log entry is attributed to this staff member -
    # must go before deleting the staff row it points at.
    db.query(AuditLog).filter(AuditLog.staff_user_id == staff.id).delete()
    db.commit()
    db.delete(staff)
    db.commit()


@pytest.fixture
def sales_attendant_token(db):
    staff = StaffUser(
        full_name="Product Test Attendant",
        email=f"prodattendant-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.SALES_ATTENDANT,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.delete(staff)
    db.commit()


@pytest.fixture
def category(db):
    category = Category(name=f"Product Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.commit()

    yield category

    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_create_product_also_creates_default_variant_and_stock(client, db, category, owner_token):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/products",
        json={
            "category_id": str(category.id),
            "name": "Collapsed Create Test Product",
            "sku": sku,
            "price": 12345.0,
            "quantity_available": 7,
        },
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    product_id = uuid.UUID(unwrap(response)["id"])

    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    assert variant is not None
    assert variant.product_id == product_id
    assert variant.price == 12345.0

    record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).first()
    assert record is not None
    assert record.quantity_available == 7

    movement = db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).first()
    assert movement is not None
    assert movement.movement_type.value == "stock_in"
    assert movement.quantity_changed == 7

    # Teardown - FK-safe order (see CLAUDE.md).
    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product_id).delete()
    db.commit()


def test_create_product_with_duplicate_sku_rolls_back_the_whole_product(client, db, category, owner_token):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"

    first = client.post(
        "/api/v1/products",
        json={"category_id": str(category.id), "name": "First Product", "sku": sku, "price": 1000.0},
        headers=_auth(owner_token),
    )
    assert first.status_code == 200
    first_product_id = uuid.UUID(unwrap(first)["id"])

    # Same SKU again - should reject with 409, and must NOT leave behind a
    # second, variant-less Product row.
    dupe = client.post(
        "/api/v1/products",
        json={"category_id": str(category.id), "name": "Second Product", "sku": sku, "price": 2000.0},
        headers=_auth(owner_token),
    )
    assert dupe.status_code == 409
    assert db.query(Product).filter(Product.name == "Second Product").first() is None

    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).first()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == first_product_id).delete()
    db.commit()


def test_product_read_exposes_default_variant_price_sku_and_gates_quantity(client, db, category, owner_token):
    # Added 2026-08-21 alongside ProductRead gaining sku/price/quantity_available
    # (see CLAUDE.md's note) - the admin dashboard needs these directly on
    # GET /products/GET /products/{id}, not a second GET /variants call.
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    create_response = client.post(
        "/api/v1/products",
        json={
            "category_id": str(category.id),
            "name": "Read Fields Test Product",
            "sku": sku,
            "price": 999.5,
            "quantity_available": 5,
        },
        headers=_auth(owner_token),
    )
    assert create_response.status_code == 200
    product_id = unwrap(create_response)["id"]

    # A public/unauthenticated GET sees price/sku (public info) but NOT the
    # real quantity - same staff-only gating VariantRead.quantity_available
    # already has, per FR-PROD-017/018.
    public_detail = client.get(f"/api/v1/products/{product_id}")
    assert public_detail.status_code == 200
    detail_data = unwrap(public_detail)
    assert detail_data["sku"] == sku
    assert detail_data["price"] == 999.5
    assert detail_data["quantity_available"] is None

    # A staff-authenticated GET sees the real number.
    staff_detail = client.get(f"/api/v1/products/{product_id}", headers=_auth(owner_token))
    assert unwrap(staff_detail)["quantity_available"] == 5

    # Same gating on the list endpoint.
    public_list = client.get("/api/v1/products", params={"search": "Read Fields Test Product"})
    public_item = next(p for p in unwrap(public_list)["items"] if p["id"] == product_id)
    assert public_item["sku"] == sku
    assert public_item["quantity_available"] is None

    staff_list = client.get(
        "/api/v1/products", params={"search": "Read Fields Test Product"}, headers=_auth(owner_token)
    )
    staff_item = next(p for p in unwrap(staff_list)["items"] if p["id"] == product_id)
    assert staff_item["quantity_available"] == 5

    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).first()
    # quantity_available=5 on create means a STOCK_IN InventoryMovement got
    # logged too - must go before the InventoryRecord it references (see
    # CLAUDE.md's FK-safe-teardown-order gotcha).
    db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
    db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == uuid.UUID(product_id)).delete()
    db.commit()


def test_update_product_does_not_require_variant_fields(client, db, category, owner_token):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    create_response = client.post(
        "/api/v1/products",
        json={"category_id": str(category.id), "name": "Editable Product", "sku": sku, "price": 500.0},
        headers=_auth(owner_token),
    )
    product_id = unwrap(create_response)["id"]

    # ProductUpdate has no sku/price/quantity_available fields at all -
    # this must succeed without them.
    update_response = client.put(
        f"/api/v1/products/{product_id}",
        json={"category_id": str(category.id), "name": "Renamed Product"},
        headers=_auth(owner_token),
    )
    assert update_response.status_code == 200
    assert unwrap(update_response)["name"] == "Renamed Product"

    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).first()
    db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == uuid.UUID(product_id)).delete()
    db.commit()


# --- Product lifecycle (deactivate/reactivate/permanent-delete), added
# 2026-08-30 for the admin "archived" tab. ---


def _create_lifecycle_product(client, category, owner_token, name):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/products",
        json={"category_id": str(category.id), "name": name, "sku": sku, "price": 500.0},
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    return unwrap(response)["id"], sku


def _cleanup_lifecycle_product(db, product_id, sku):
    # Safe to call even if permanent-delete already removed everything -
    # every step below no-ops if its target row is already gone.
    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    if variant is not None:
        record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).first()
        if record is not None:
            db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record.id).delete()
            db.commit()
            db.query(InventoryRecord).filter(InventoryRecord.id == record.id).delete()
            db.commit()
        db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
        db.commit()
    db.query(Product).filter(Product.id == uuid.UUID(product_id)).delete()
    db.commit()


def test_permanent_delete_rejects_sales_attendant(client, db, category, owner_token, sales_attendant_token):
    # Deliberately narrower than the rest of this router - see
    # routers/products.py's delete_product_permanent comment.
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product A")

    response = client.delete(f"/api/v1/products/{product_id}/permanent", headers=_auth(sales_attendant_token))
    assert response.status_code == 403

    _cleanup_lifecycle_product(db, product_id, sku)


def test_permanent_delete_rejects_while_still_active(client, db, category, owner_token):
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product B")

    response = client.delete(f"/api/v1/products/{product_id}/permanent", headers=_auth(owner_token))
    assert response.status_code == 409

    _cleanup_lifecycle_product(db, product_id, sku)


def test_permanent_delete_rejects_before_30_days_elapsed(client, db, category, owner_token):
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product C")

    deactivate_response = client.delete(f"/api/v1/products/{product_id}", headers=_auth(owner_token))
    assert deactivate_response.status_code == 204

    response = client.delete(f"/api/v1/products/{product_id}/permanent", headers=_auth(owner_token))
    assert response.status_code == 409

    _cleanup_lifecycle_product(db, product_id, sku)


def test_reactivate_clears_deactivated_at(client, db, category, owner_token):
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product D")
    client.delete(f"/api/v1/products/{product_id}", headers=_auth(owner_token))

    product = db.get(Product, uuid.UUID(product_id))
    assert product.deactivated_at is not None

    response = client.patch(f"/api/v1/products/{product_id}/reactivate", headers=_auth(owner_token))
    assert response.status_code == 200
    body = unwrap(response)
    assert body["is_active"] is True
    assert body["deactivated_at"] is None

    db.refresh(product)
    assert product.deactivated_at is None

    _cleanup_lifecycle_product(db, product_id, sku)


def test_permanent_delete_rejects_when_order_history_exists(client, db, category, owner_token):
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product E")
    variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()

    order = Order(
        order_number=f"JN-LIFECYCLE-{uuid.uuid4().hex[:8]}",
        guest_full_name="History Buyer",
        guest_phone_number="+256700000001",
        delivery_address="Somewhere",
        district="Somewhere",
        subtotal=500.0,
        total=500.0,
    )
    db.add(order)
    db.flush()
    order_item = OrderItem(
        order_id=order.id,
        variant_id=variant.id,
        product_name_snapshot="Lifecycle Test Product E",
        quantity=1,
        unit_price=500.0,
        line_total=500.0,
    )
    db.add(order_item)
    db.commit()

    client.delete(f"/api/v1/products/{product_id}", headers=_auth(owner_token))
    product = db.get(Product, uuid.UUID(product_id))
    # Backdate past the 30-day window directly, rather than actually
    # waiting - this test is specifically about the ORDER HISTORY check,
    # not the elapsed-time one (already covered above).
    product.deactivated_at = datetime.now(timezone.utc) - timedelta(days=31)
    db.commit()

    response = client.delete(f"/api/v1/products/{product_id}/permanent", headers=_auth(owner_token))
    assert response.status_code == 409

    db.query(OrderItem).filter(OrderItem.id == order_item.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    _cleanup_lifecycle_product(db, product_id, sku)


def test_permanent_delete_succeeds_once_eligible(client, db, category, owner_token):
    product_id, sku = _create_lifecycle_product(client, category, owner_token, "Lifecycle Test Product F")
    variant_id = db.query(ProductVariant).filter(ProductVariant.sku == sku).first().id
    record_id = db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant_id).first().id

    client.delete(f"/api/v1/products/{product_id}", headers=_auth(owner_token))
    product = db.get(Product, uuid.UUID(product_id))
    product.deactivated_at = datetime.now(timezone.utc) - timedelta(days=31)
    db.commit()

    response = client.delete(f"/api/v1/products/{product_id}/permanent", headers=_auth(owner_token))
    assert response.status_code == 204

    assert db.get(Product, uuid.UUID(product_id)) is None
    assert db.get(ProductVariant, variant_id) is None
    assert db.get(InventoryRecord, record_id) is None
    assert db.query(InventoryMovement).filter(InventoryMovement.inventory_record_id == record_id).first() is None
