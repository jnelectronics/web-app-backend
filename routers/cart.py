# Cart is identified differently from every other domain so far: not by an
# id in the URL, but by WHO is asking - either a logged-in customer's
# Authorization header, or an X-Guest-Token header for someone browsing
# without an account. get_current_cart below is what resolves either case
# down to a single real Cart row, creating one the first time it's needed.

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Cart, CartItem, CartStatus, ProductVariant
from schemas import CartItemAdd, CartItemUpdate, CartRead
from security import decode_token_claims

router = APIRouter(prefix="/cart", tags=["cart"], route_class=EnvelopeRoute)


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


def _build_cart_read(cart: Cart, db: Session) -> CartRead:
    # Shared by every route below - re-reads this cart's items fresh from
    # the database and shapes them into the response, so every endpoint
    # returns the same up-to-date view of the cart after whatever it did.
    items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()
    return CartRead(id=cart.id, items=items)


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
