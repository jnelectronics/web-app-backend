# Covers the post-delivery experience rating (added 2026-08-30, client
# UAT request) - the public, token-based prefill + submit pair
# (routers/order_ratings.py), per Nyson's confirmed spec: Option A (public
# token link), 30-day TTL, 1-5 stars + optional 500-char comment.

import uuid

import pytest

from conftest import uncategorized_group_id, unwrap
from models import Category, Order, OrderItem, OrderRating, OrderStatus, Product, ProductVariant
import security
from security import create_order_rating_token


@pytest.fixture
def delivered_order(db):
    category = Category(
        name=f"Rating Test Category {uuid.uuid4().hex[:8]}", category_group_id=uncategorized_group_id(db)
    )
    db.add(category)
    db.flush()
    product = Product(category_id=category.id, name="Rating Test Product")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", price=1000.0)
    db.add(variant)
    db.flush()

    order = Order(
        order_number=f"JN-RATING-{uuid.uuid4().hex[:8]}",
        guest_full_name="Rating Buyer",
        guest_phone_number="+256700000099",
        guest_email="rating-buyer@example.com",
        delivery_address="Test Address",
        district="Test District",
        status=OrderStatus.DELIVERED,
        subtotal=2000.0,
        total=2000.0,
    )
    db.add(order)
    db.flush()
    order_item = OrderItem(
        order_id=order.id,
        variant_id=variant.id,
        product_name_snapshot="Rating Test Product",
        quantity=2,
        unit_price=1000.0,
        line_total=2000.0,
    )
    db.add(order_item)
    db.commit()

    yield order

    db.query(OrderRating).filter(OrderRating.order_id == order.id).delete()
    db.commit()
    db.query(OrderItem).filter(OrderItem.id == order_item.id).delete()
    db.commit()
    db.query(Order).filter(Order.id == order.id).delete()
    db.commit()
    db.query(ProductVariant).filter(ProductVariant.id == variant.id).delete()
    db.commit()
    db.query(Product).filter(Product.id == product.id).delete()
    db.commit()
    db.query(Category).filter(Category.id == category.id).delete()
    db.commit()


def test_prefill_invalid_token_returns_invalid_status(client):
    response = client.get("/api/v1/public/order-ratings/not-a-real-token")
    assert response.status_code == 200
    assert unwrap(response)["rating_status"] == "invalid"


def test_prefill_expired_token_returns_expired_status(client, delivered_order, monkeypatch):
    # A negative expiry mints a token that's already expired the instant
    # it's created - simplest way to get a genuinely expired JWT without
    # actually waiting 30 days.
    monkeypatch.setattr(security, "ORDER_RATING_TOKEN_EXPIRE_MINUTES", -1)
    token = create_order_rating_token(str(delivered_order.id))

    response = client.get(f"/api/v1/public/order-ratings/{token}")
    assert response.status_code == 200
    assert unwrap(response)["rating_status"] == "expired"


def test_prefill_eligible_then_submit_then_already_rated(client, db, delivered_order):
    token = create_order_rating_token(str(delivered_order.id))

    prefill = client.get(f"/api/v1/public/order-ratings/{token}")
    assert prefill.status_code == 200
    prefill_body = unwrap(prefill)
    assert prefill_body["rating_status"] == "eligible"
    assert prefill_body["order_number"] == delivered_order.order_number
    assert prefill_body["item_count"] == 2

    submit = client.post(
        f"/api/v1/public/order-ratings/{token}", json={"score": 5, "comment": "Great service!"}
    )
    assert submit.status_code == 201
    submit_body = unwrap(submit)
    assert submit_body["score"] == 5
    assert submit_body["comment"] == "Great service!"
    assert submit_body["order_id"] == str(delivered_order.id)

    # A second submit for the same order is rejected...
    second_submit = client.post(f"/api/v1/public/order-ratings/{token}", json={"score": 1})
    assert second_submit.status_code == 409

    # ...and prefill now reports the already-submitted rating instead.
    prefill_again = client.get(f"/api/v1/public/order-ratings/{token}")
    prefill_again_body = unwrap(prefill_again)
    assert prefill_again_body["rating_status"] == "already_rated"
    assert prefill_again_body["score"] == 5
    assert prefill_again_body["comment"] == "Great service!"


def test_submit_rejects_score_out_of_range(client, delivered_order):
    token = create_order_rating_token(str(delivered_order.id))
    response = client.post(f"/api/v1/public/order-ratings/{token}", json={"score": 6})
    assert response.status_code == 422


def test_submit_rejects_order_that_is_not_delivered(client, db, delivered_order):
    delivered_order.status = OrderStatus.CONFIRMED
    db.commit()
    token = create_order_rating_token(str(delivered_order.id))

    response = client.post(f"/api/v1/public/order-ratings/{token}", json={"score": 4})
    assert response.status_code == 404
