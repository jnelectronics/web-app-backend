# Covers the two catalog extensions built on top of Products/Variants:
# variant_attributes (EAV, via the `attributes` field on VariantCreate) and
# product_images (max 5 per product, exactly one primary - BR-PROD-004/005).

import uuid

import pytest

from conftest import unwrap
from models import Category, Product, ProductImage, ProductVariant, StaffRole, StaffUser, VariantAttribute
from security import create_access_token, hash_password


@pytest.fixture
def inventory_manager_token(db):
    staff = StaffUser(
        full_name="Catalog Manager",
        email=f"catalogmgr-{uuid.uuid4().hex[:8]}@example.com",
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
    category = Category(name=f"Ext Test Category {uuid.uuid4().hex[:8]}")
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


def test_variant_attributes_created_and_replaced(client, product, inventory_manager_token):
    response = client.post(
        "/api/v1/variants",
        json={
            "product_id": str(product.id),
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "price": 1000.0,
            "attributes": {"color": "Black", "capacity": "128GB"},
        },
        headers=_auth(inventory_manager_token),
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
        headers=_auth(inventory_manager_token),
    )
    assert response.status_code == 200
    assert unwrap(response)["attributes"] == {"color": "White"}


def test_product_image_lifecycle_and_primary_cap(client, product, inventory_manager_token, mock_cloudinary):
    headers = _auth(inventory_manager_token)
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
