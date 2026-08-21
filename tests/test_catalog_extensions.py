# Covers the two catalog extensions built on top of Products/Variants:
# variant_attributes (EAV, via the `attributes` field on VariantCreate) and
# product_images (max 5 per product, exactly one primary - BR-PROD-004/005).

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import AuditLog, Category, InventoryRecord, Product, ProductDiscount, ProductImage, ProductVariant, StaffRole, StaffUser, VariantAttribute
from security import create_access_token, hash_password


@pytest.fixture
def owner_token(db):
    staff = StaffUser(
        full_name="Catalog Manager",
        email=f"catalogmgr-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
        role=StaffRole.OWNER,
    )
    db.add(staff)
    db.commit()

    yield create_access_token(subject=str(staff.id), account_type="staff")

    db.query(StaffUser).filter(StaffUser.id == staff.id).delete()
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# A tiny, deliberately-fake "image" - the mocked upload_image below never
# actually inspects the bytes (that's Cloudinary's real integration's job,
# verified separately against real sandbox credentials, not on every
# pytest run - same reasoning as test_payments.py's mock_pesapal). Only
# file.content_type (set explicitly below) matters to routers/products.py's
# own "must be an image" check.
FAKE_IMAGE_BYTES = b"fake image bytes"


def _fake_image_file(name="test.png"):
    return {"file": (name, FAKE_IMAGE_BYTES, "image/png")}


@pytest.fixture
def mock_cloudinary(monkeypatch):
    # Patched where it's USED (routers.products), not where it's defined
    # (cloudinary_client) - routers/products.py already imported these
    # names directly (`from cloudinary_client import ...`), so patching
    # cloudinary_client itself wouldn't affect the reference
    # routers/products.py is holding. Same gotcha as test_payments.py's
    # mock_pesapal fixture.
    deleted_public_ids = []

    def fake_upload_image(file_bytes, filename):
        return {
            "secure_url": f"https://res.cloudinary.com/test/{uuid.uuid4().hex}/{filename}",
            "public_id": f"jn_electronics/products/{uuid.uuid4().hex}",
        }

    def fake_delete_image(public_id):
        deleted_public_ids.append(public_id)

    monkeypatch.setattr("routers.products.is_configured", lambda: True)
    monkeypatch.setattr("routers.products.upload_image", fake_upload_image)
    monkeypatch.setattr("routers.products.delete_image", fake_delete_image)
    return deleted_public_ids


@pytest.fixture
def product(db):
    category = Category(name=f"Ext Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Ext Test Product")
    db.add(product)
    db.commit()

    yield product

    db.query(ProductImage).filter(ProductImage.product_id == product.id).delete()
    db.commit()
    db.query(VariantAttribute).filter(
        VariantAttribute.variant_id.in_(
            db.query(ProductVariant.id).filter(ProductVariant.product_id == product.id)
        )
    ).delete(synchronize_session=False)
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.product_id == product.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_variant_attributes_created_and_replaced(client, product, owner_token):
    response = client.post(
        "/api/v1/variants",
        json={
            "product_id": str(product.id),
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "price": 1000.0,
            "attributes": {"color": "Black", "capacity": "128GB"},
        },
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    variant = unwrap(response)
    assert variant["attributes"] == {"color": "Black", "capacity": "128GB"}

    response = client.get(f"/api/v1/variants/{variant['id']}")
    assert response.status_code == 200
    assert unwrap(response)["attributes"] == {"color": "Black", "capacity": "128GB"}

    # A full update REPLACES the attribute set, not merges it
    response = client.put(
        f"/api/v1/variants/{variant['id']}",
        json={
            "product_id": str(product.id),
            "sku": variant["sku"],
            "price": 1200.0,
            "attributes": {"color": "White"},
        },
        headers=_auth(owner_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["attributes"] == {"color": "White"}


def test_check_sku_endpoint(client, product, owner_token):
    # Not in the original spec - added so the admin product form's SKU
    # generator can ask a single question ("is this SKU taken?") instead
    # of loading up to 500 products client-side to check for a collision
    # itself.
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    headers = _auth(owner_token)

    response = client.get("/api/v1/variants/check-sku", params={"sku": sku}, headers=headers)
    assert response.status_code == 200
    body = unwrap(response)
    assert body == {"sku": sku, "exists": False}

    client.post(
        "/api/v1/variants",
        json={"product_id": str(product.id), "sku": sku, "price": 1000.0},
        headers=headers,
    )

    response = client.get("/api/v1/variants/check-sku", params={"sku": sku}, headers=headers)
    assert unwrap(response) == {"sku": sku, "exists": True}

    # Staff-only - matches create_variant/update_variant's own role gate.
    response = client.get("/api/v1/variants/check-sku", params={"sku": sku})
    assert response.status_code == 401


def test_product_image_lifecycle_and_primary_cap(client, product, owner_token, mock_cloudinary):
    headers = _auth(owner_token)
    image_ids = []

    for i in range(5):
        response = client.post(
            f"/api/v1/products/{product.id}/images",
            files=_fake_image_file(f"img{i}.png"),
            data={"display_order": i},
            headers=headers,
        )
        assert response.status_code == 200
        uploaded = unwrap(response)
        # Came from the (mocked) Cloudinary response, not a client-supplied
        # URL - this is the whole point of the real integration.
        assert uploaded["image_url"].startswith("https://res.cloudinary.com/")
        assert uploaded["cloudinary_public_id"] is not None
        image_ids.append(uploaded["id"])

    # First image uploaded is automatically primary
    response = client.get("/api/v1/products")  # sanity: catalog still browsable
    assert response.status_code == 200

    # A 6th image is rejected - max 5 per product (BR-PROD-004)
    response = client.post(
        f"/api/v1/products/{product.id}/images",
        files=_fake_image_file("img6.png"),
        headers=headers,
    )
    assert response.status_code == 400

    # A non-image file is rejected before ever reaching Cloudinary
    response = client.post(
        f"/api/v1/products/{product.id}/images",
        files={"file": ("not-an-image.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 400

    # Set the second image as primary - the first should no longer be primary
    response = client.patch(
        f"/api/v1/products/{product.id}/images/{image_ids[1]}/primary", headers=headers
    )
    assert response.status_code == 200
    assert unwrap(response)["is_primary"] is True

    response = client.put(
        f"/api/v1/products/{product.id}/images/{image_ids[0]}",
        files=_fake_image_file("replaced.png"),
        data={"display_order": 0},
        headers=headers,
    )
    assert response.status_code == 200
    assert unwrap(response)["is_primary"] is False
    # Replacing an image deletes the OLD Cloudinary asset it replaced
    assert len(mock_cloudinary) == 1

    response = client.delete(f"/api/v1/products/{product.id}/images/{image_ids[4]}", headers=headers)
    assert response.status_code == 204
    # Deleting an image also deletes its Cloudinary asset
    assert len(mock_cloudinary) == 2


def test_product_list_filtering_search_sort_and_pagination(client, db, owner_token):
    headers = _auth(owner_token)
    group_id = uncategorized_group_id(db)
    category = Category(name=f"Filter Test Category {uuid.uuid4().hex[:8]}", category_group_id=group_id)
    other_category = Category(name=f"Filter Test Other Category {uuid.uuid4().hex[:8]}", category_group_id=group_id)
    db.add_all([category, other_category])
    db.commit()

    try:
        apple_response = client.post(
            "/api/v1/products",
            json={
                "category_id": str(category.id),
                "name": "Apple Widget",
                "description": "A shiny gadget",
                "is_featured": True,
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "price": 1000.0,
            },
            headers=headers,
        )
        banana_response = client.post(
            "/api/v1/products",
            json={
                "category_id": str(category.id),
                "name": "Banana Widget",
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "price": 1000.0,
            },
            headers=headers,
        )
        other_category_response = client.post(
            "/api/v1/products",
            json={
                "category_id": str(other_category.id),
                "name": "Cherry Widget",
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "price": 1000.0,
            },
            headers=headers,
        )
        assert apple_response.status_code == 200
        assert banana_response.status_code == 200
        assert other_category_response.status_code == 200
        apple_id = unwrap(apple_response)["id"]

        # category filter
        response = client.get(f"/api/v1/products?category={category.id}")
        body = unwrap(response)
        assert {p["name"] for p in body["items"]} == {"Apple Widget", "Banana Widget"}
        assert body["pagination"]["total_records"] == 2

        # search matches name OR description
        response = client.get("/api/v1/products?search=shiny")
        assert {p["name"] for p in unwrap(response)["items"]} == {"Apple Widget"}

        # featured filter
        response = client.get("/api/v1/products?featured=true")
        names = {p["name"] for p in unwrap(response)["items"]}
        assert "Apple Widget" in names
        assert "Banana Widget" not in names

        # sort=-name (descending)
        response = client.get(f"/api/v1/products?category={category.id}&sort=-name")
        names_in_order = [p["name"] for p in unwrap(response)["items"]]
        assert names_in_order == ["Banana Widget", "Apple Widget"]

        # pagination metadata
        response = client.get(f"/api/v1/products?category={category.id}&limit=1&skip=0")
        body = unwrap(response)
        assert len(body["items"]) == 1
        assert body["pagination"] == {"page": 1, "page_size": 1, "total_records": 2, "total_pages": 2}

        # discounted filter - false until a discount actually exists
        response = client.get(f"/api/v1/products?category={category.id}&discounted=true")
        assert unwrap(response)["items"] == []

        client.post(
            f"/api/v1/products/{apple_id}/discounts",
            json={"discount_type": "percentage", "discount_value": 10.0},
            headers=headers,
        )
        response = client.get(f"/api/v1/products?category={category.id}&discounted=true")
        assert [p["name"] for p in unwrap(response)["items"]] == ["Apple Widget"]
    finally:
        all_category_ids = [category.id, other_category.id]
        product_ids = [p.id for p in db.query(Product).filter(Product.category_id.in_(all_category_ids)).all()]
        db.query(AuditLog).filter(
            AuditLog.resource_type == "product", AuditLog.resource_id.in_(product_ids)
        ).delete(synchronize_session=False)
        db.commit()
        db.query(ProductDiscount).filter(ProductDiscount.product_id.in_(product_ids)).delete(synchronize_session=False)
        db.commit()
        # POST /products now also creates a default variant + stock record
        # (see CLAUDE.md's 2026-08-20 note) - clean those up before the
        # products they point at.
        variant_ids = [v.id for v in db.query(ProductVariant).filter(ProductVariant.product_id.in_(product_ids)).all()]
        db.query(InventoryRecord).filter(InventoryRecord.variant_id.in_(variant_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(ProductVariant).filter(ProductVariant.id.in_(variant_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Product).filter(Product.id.in_(product_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Category).filter(Category.id.in_(all_category_ids)).delete(synchronize_session=False)
        db.commit()


def test_product_read_includes_new_fields_and_computed_discount(client, db, owner_token):
    headers = _auth(owner_token)
    category = Category(name=f"Field Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.commit()

    try:
        response = client.post(
            "/api/v1/products",
            json={
                "category_id": str(category.id),
                "name": "Field Test Product",
                "description": "A great product",
                "is_featured": True,
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "price": 1000.0,
            },
            headers=headers,
        )
        assert response.status_code == 200
        product = unwrap(response)
        assert product["description"] == "A great product"
        assert product["is_featured"] is True
        # Server-generated from the name, not client-supplied
        assert product["slug"] == "field-test-product"
        assert product["images"] == []
        assert product["is_discounted"] is False

        # A second product with the SAME name gets a disambiguated slug,
        # not a duplicate (the column is unique) or a crash
        response2 = client.post(
            "/api/v1/products",
            json={
                "category_id": str(category.id),
                "name": "Field Test Product",
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "price": 1000.0,
            },
            headers=headers,
        )
        assert response2.status_code == 200
        assert unwrap(response2)["slug"] == "field-test-product-2"

        # A currently-active discount flips is_discounted to True on a
        # plain public GET, not just the staff-created response
        discount_response = client.post(
            f"/api/v1/products/{product['id']}/discounts",
            json={"discount_type": "percentage", "discount_value": 15.0},
            headers=headers,
        )
        assert discount_response.status_code == 200

        response = client.get(f"/api/v1/products/{product['id']}")
        assert unwrap(response)["is_discounted"] is True
        # The second product has no discount - still False
        response = client.get(f"/api/v1/products/{response2.json()['data']['id']}")
        assert unwrap(response)["is_discounted"] is False
    finally:
        product_ids = [p.id for p in db.query(Product).filter(Product.category_id == category.id).all()]
        # product.create writes an audit_logs row referencing the acting
        # staff member (write_audit_log) - has to go before
        # owner_token's OWN teardown tries to delete that
        # StaffUser row, per the LIFO fixture-teardown-order gotcha in
        # CLAUDE.md (this fixture's teardown runs AFTER this one, so it
        # can't defend against a reference this test itself created).
        db.query(AuditLog).filter(
            AuditLog.resource_type == "product", AuditLog.resource_id.in_(product_ids)
        ).delete(synchronize_session=False)
        db.commit()
        db.query(ProductDiscount).filter(ProductDiscount.product_id.in_(product_ids)).delete(synchronize_session=False)
        db.commit()
        # POST /products now also creates a default variant + stock record
        # (see CLAUDE.md's 2026-08-20 note) - clean those up before the
        # products they point at.
        variant_ids = [v.id for v in db.query(ProductVariant).filter(ProductVariant.product_id.in_(product_ids)).all()]
        db.query(InventoryRecord).filter(InventoryRecord.variant_id.in_(variant_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(ProductVariant).filter(ProductVariant.id.in_(variant_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Product).filter(Product.id.in_(product_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Category).filter(Category.id == category.id).delete()
        db.commit()
