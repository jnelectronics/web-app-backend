# No dedicated cart test file existed before - cart was only exercised
# incidentally via other domains' checkout flows. This covers the display
# fields CartItemRead gained (product name/variant label/sku/image_url),
# fetched fresh at read time rather than snapshotted (only the price is
# snapshotted - see the schema's own comment).

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import Cart, CartItem, Category, Customer, InventoryRecord, Product, ProductImage, ProductVariant
from security import create_access_token, hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def cart_setup(db):
    category = Category(name=f"Cart Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db))
    db.add(category)
    db.flush()

    product = Product(category_id=category.id, name="Cart Test Product")
    db.add(product)
    db.flush()

    variant = ProductVariant(
        product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=25000.0, variant_label="Black / 64GB"
    )
    db.add(variant)
    db.flush()

    image = ProductImage(product_id=product.id, image_url="https://res.cloudinary.com/test/cart.png", is_primary=True)
    db.add(image)

    db.add(InventoryRecord(variant_id=variant.id, quantity_available=10))

    customer = Customer(
        full_name="Cart Test Customer",
        email=f"carttest-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123"),
    )
    db.add(customer)
    db.commit()

    token = create_access_token(subject=str(customer.id), account_type="customer")

    yield {"product": product, "variant": variant, "customer": customer, "token": token}

    cart_ids = [c.id for c in db.query(Cart).filter(Cart.customer_id == customer.id).all()]
    if cart_ids:
        db.query(CartItem).filter(CartItem.cart_id.in_(cart_ids)).delete(synchronize_session=False)
        db.commit()
        db.query(Cart).filter(Cart.id.in_(cart_ids)).delete(synchronize_session=False)
        db.commit()
    db.query(InventoryRecord).filter(InventoryRecord.variant_id == variant.id).delete()
    db.commit()
    db.query(Customer).filter(Customer.id == customer.id).delete()
    db.commit()
    db.query(ProductImage).filter(ProductImage.product_id == product.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_cart_item_includes_display_fields(client, cart_setup):
    headers = _auth(cart_setup["token"])
    variant = cart_setup["variant"]

    response = client.post(
        "/api/v1/cart/items", json={"variant_id": str(variant.id), "quantity": 2}, headers=headers
    )
    assert response.status_code == 200
    cart = unwrap(response)
    assert len(cart["items"]) == 1
    item = cart["items"][0]
    assert item["product_id"] == str(cart_setup["product"].id)
    assert item["product_name"] == "Cart Test Product"
    assert item["variant_label"] == "Black / 64GB"
    assert item["sku"] == variant.sku
    assert item["image_url"] == "https://res.cloudinary.com/test/cart.png"
    # unit_price_snapshot is still the price AT ADD TIME, not display data
    assert item["unit_price_snapshot"] == 25000.0

    response = client.get("/api/v1/cart", headers=headers)
    assert response.status_code == 200
    assert unwrap(response)["items"][0]["product_name"] == "Cart Test Product"


def test_guest_cart_merges_into_customer_cart_on_login(client, cart_setup):
    variant = cart_setup["variant"]
    customer_headers = _auth(cart_setup["token"])
    guest_token = f"guest-merge-{uuid.uuid4().hex}"
    guest_headers = {"X-Guest-Token": guest_token}

    # Customer already has 2 of this variant in their own cart...
    response = client.post(
        "/api/v1/cart/items", json={"variant_id": str(variant.id), "quantity": 2}, headers=customer_headers
    )
    assert response.status_code == 200

    # ...and, separately (as a guest, no account), adds 3 more of the SAME
    # variant before ever logging in.
    response = client.post(
        "/api/v1/cart/items", json={"variant_id": str(variant.id), "quantity": 3}, headers=guest_headers
    )
    assert response.status_code == 200

    # Logging in, carrying the guest token along, merges the two -
    # quantities for the same variant combine rather than either being lost.
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": cart_setup["customer"].email, "password": "Password123"},
        headers=guest_headers,
    )
    assert response.status_code == 200
    new_access_token = unwrap(response)["access_token"]

    response = client.get("/api/v1/cart", headers=_auth(new_access_token))
    assert response.status_code == 200
    items = unwrap(response)["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 5

    # The guest cart itself no longer looks "active" - a fresh add-to-cart
    # with the same (now-stale) guest token starts a brand new cart rather
    # than silently reusing the merged-away one.
    response = client.get("/api/v1/cart", headers=guest_headers)
    assert unwrap(response)["items"] == []
