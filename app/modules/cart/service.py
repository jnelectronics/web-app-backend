import uuid

from sqlalchemy.orm import Session

from app.modules.cart.models import Cart, CartItem
from app.modules.cart.repository import CartRepository
from app.modules.cart.schemas import CartItemUpsert
from app.modules.products.repository import ProductVariantRepository


class CartService:
    """FR-CART-001-010: guest carts key off X-Guest-Token; registered ones off customer_id."""

    def __init__(self, db: Session) -> None:
        self.repository = CartRepository(db)
        self.variants = ProductVariantRepository(db)

    def get_or_create_cart(self, *, customer_id: uuid.UUID | None, guest_token: str | None) -> Cart:
        cart = (
            self.repository.get_by_customer(customer_id)
            if customer_id
            else self.repository.get_by_guest_token(guest_token)
        )
        if cart:
            return cart
        return self.repository.create(Cart(customer_id=customer_id, guest_token=guest_token))

    def upsert_item(
        self, *, customer_id: uuid.UUID | None, guest_token: str | None, payload: CartItemUpsert
    ) -> Cart:
        cart = self.get_or_create_cart(customer_id=customer_id, guest_token=guest_token)
        variant = self.variants.get(payload.variant_id)

        existing = self.repository.get_item(cart.id, payload.variant_id)
        if existing:
            existing.quantity = payload.quantity
        else:
            cart.items.append(
                CartItem(
                    variant_id=payload.variant_id,
                    quantity=payload.quantity,
                    unit_price_snapshot=variant.price,
                )
            )
        self.repository.save(cart)
        return cart
