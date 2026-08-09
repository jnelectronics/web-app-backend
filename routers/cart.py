# Cart is identified differently from every other domain so far: not by an
# id in the URL, but by WHO is asking - either a logged-in customer's
# Authorization header, or an X-Guest-Token header for someone browsing
# without an account. get_current_cart below is what resolves either case
# down to a single real Cart row, creating one the first time it's needed.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Cart, CartItem, CartStatus, DiscountType, Product, ProductDiscount, ProductImage, ProductVariant
from schemas import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from security import decode_token_claims

router = APIRouter(prefix="/cart", tags=["cart"], route_class=EnvelopeRoute)


def _resolve_discounted_price(db: Session, product_id: uuid.UUID | None, original_price: float) -> float | None:
    # Same "currently active" window check as routers/products.py's
    # _is_product_discounted, duplicated rather than imported - it's three
    # lines of a query, not a business rule complex enough to be worth a
    # cross-router import for (see CLAUDE.md on this project's general
    # preference for small, domain-scoped duplication over that kind of
    # coupling). None means "no discount applies right now."
    if product_id is None:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    discount = (
        db.query(ProductDiscount)
        .filter(
            ProductDiscount.product_id == product_id,
            ProductDiscount.is_active == True,  # noqa: E712
            or_(ProductDiscount.starts_at.is_(None), ProductDiscount.starts_at <= now),
            or_(ProductDiscount.ends_at.is_(None), ProductDiscount.ends_at >= now),
        )
        .first()
    )
    if discount is None:
        return None
    if discount.discount_type == DiscountType.PERCENTAGE:
        return round(original_price * (1 - discount.discount_value / 100), 2)
    return max(0.0, round(original_price - discount.discount_value, 2))


def get_current_cart(
    authorization: str | None = Header(default=None),
    x_guest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Cart:
    # Authorization takes priority: if a bearer token is present at all, we
    # treat this as a registered-customer request, even if a guest token
    # was also (mistakenly) sent alongside it.
    if authorization is not None:
        # Expected shape: "Bearer <token>" - split off the scheme prefix.
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Invalid Authorization header")

        claims = decode_token_claims(token)
        # Only a customer token counts here - a staff token being present
        # shouldn't let staff accidentally "have a cart" of their own.
        if claims is None or claims.get("type") != "customer":
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        customer_id = claims["sub"]

        cart = (
            db.query(Cart)
            .filter(Cart.customer_id == uuid.UUID(customer_id), Cart.status == CartStatus.ACTIVE)
            .first()
        )
        if cart is None:
            cart = Cart(customer_id=uuid.UUID(customer_id))
            db.add(cart)
            db.commit()
            db.refresh(cart)
        return cart

    if x_guest_token is not None:
        cart = (
            db.query(Cart)
            .filter(Cart.guest_token == x_guest_token, Cart.status == CartStatus.ACTIVE)
            .first()
        )
        if cart is None:
            cart = Cart(guest_token=x_guest_token)
            db.add(cart)
            db.commit()
            db.refresh(cart)
        return cart

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide either an Authorization header or an X-Guest-Token header",
    )


def merge_guest_cart_into_customer(db: Session, customer_id: uuid.UUID, guest_token: str) -> None:
    # Called from routers/auth.py's login/register/google_sign_in (not in
    # the original spec - added 2026-08-06) when a request carries BOTH a
    # customer identity and an X-Guest-Token: a shopper who added items
    # anonymously and only THEN logged in shouldn't lose them.
    #
    # Deliberately does NOT commit itself - the caller (auth.py) is already
    # inside its own transaction issuing tokens; this just stages the
    # changes into that same commit, so a merge failure can't leave tokens
    # issued but the cart half-merged.
    guest_cart = (
        db.query(Cart).filter(Cart.guest_token == guest_token, Cart.status == CartStatus.ACTIVE).first()
    )
    if guest_cart is None:
        return  # No active guest cart under this token - nothing to merge.

    guest_items = db.query(CartItem).filter(CartItem.cart_id == guest_cart.id).all()
    if not guest_items:
        return

    customer_cart = (
        db.query(Cart).filter(Cart.customer_id == customer_id, Cart.status == CartStatus.ACTIVE).first()
    )
    if customer_cart is None:
        customer_cart = Cart(customer_id=customer_id)
        db.add(customer_cart)
        db.flush()  # assigns customer_cart.id for the items below

    for guest_item in guest_items:
        existing = (
            db.query(CartItem)
            .filter(CartItem.cart_id == customer_cart.id, CartItem.variant_id == guest_item.variant_id)
            .first()
        )
        if existing is not None:
            # Same variant already in the customer's own cart - combine
            # quantities rather than violate the UNIQUE(cart_id, variant_id)
            # constraint with a second row for it.
            existing.quantity += guest_item.quantity
            db.delete(guest_item)
        else:
            # Move the row itself rather than delete-and-recreate - keeps
            # its own id/created_at, and is simpler than copying every field.
            guest_item.cart_id = customer_cart.id

    # "Converted" already means "this cart's job is done, its contents now
    # live on as something else" elsewhere in this codebase (checkout
    # marks a cart CONVERTED once its items become an Order) - reused here
    # for the same reason, rather than adding a new CartStatus value for
    # one more way a cart's life can end.
    guest_cart.status = CartStatus.CONVERTED


def _build_cart_read(cart: Cart, db: Session) -> CartRead:
    # Shared by every route below - re-reads this cart's items fresh from
    # the database and shapes them into the response, so every endpoint
    # returns the same up-to-date view of the cart after whatever it did.
    items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()

    item_reads = []
    for item in items:
        variant = db.get(ProductVariant, item.variant_id)
        product = db.get(Product, variant.product_id) if variant is not None else None
        primary_image = (
            db.query(ProductImage)
            .filter(ProductImage.product_id == product.id, ProductImage.is_primary == True)  # noqa: E712
            .first()
            if product is not None
            else None
        )
        item_reads.append(
            CartItemRead(
                id=item.id,
                variant_id=item.variant_id,
                product_id=product.id if product is not None else None,
                product_name=product.name if product is not None else None,
                variant_label=variant.variant_label if variant is not None else None,
                sku=variant.sku if variant is not None else None,
                image_url=primary_image.image_url if primary_image is not None else None,
                quantity=item.quantity,
                unit_price_snapshot=item.unit_price_snapshot,
                original_price=item.unit_price_snapshot,
                discounted_price=_resolve_discounted_price(
                    db, product.id if product is not None else None, item.unit_price_snapshot
                ),
            )
        )
    return CartRead(id=cart.id, items=item_reads)


@router.get("", response_model=CartRead)
def read_cart(cart: Cart = Depends(get_current_cart), db: Session = Depends(get_db)):
    return _build_cart_read(cart, db)


@router.post("/items", response_model=CartRead)
def add_cart_item(
    item: CartItemAdd, cart: Cart = Depends(get_current_cart), db: Session = Depends(get_db)
):
    variant = db.get(ProductVariant, item.variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    existing = (
        db.query(CartItem)
        .filter(CartItem.cart_id == cart.id, CartItem.variant_id == item.variant_id)
        .first()
    )
    if existing is not None:
        # Same variant added again - increment the existing line rather
        # than violate the UNIQUE(cart_id, variant_id) constraint.
        existing.quantity += item.quantity
    else:
        db.add(
            CartItem(
                cart_id=cart.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                # Snapshot the variant's CURRENT price at the moment it's added.
                unit_price_snapshot=variant.price,
            )
        )
    db.commit()
    return _build_cart_read(cart, db)


@router.patch("/items/{item_id}", response_model=CartRead)
def update_cart_item(
    item_id: uuid.UUID,
    update: CartItemUpdate,
    cart: Cart = Depends(get_current_cart),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(CartItem).filter(CartItem.id == item_id, CartItem.cart_id == cart.id).first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Cart item not found")

    existing.quantity = update.quantity
    db.commit()
    return _build_cart_read(cart, db)


@router.delete("/items/{item_id}", response_model=CartRead)
def remove_cart_item(
    item_id: uuid.UUID, cart: Cart = Depends(get_current_cart), db: Session = Depends(get_db)
):
    existing = (
        db.query(CartItem).filter(CartItem.id == item_id, CartItem.cart_id == cart.id).first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Cart item not found")

    db.delete(existing)
    db.commit()
    return _build_cart_read(cart, db)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_cart(cart: Cart = Depends(get_current_cart), db: Session = Depends(get_db)):
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    db.commit()
