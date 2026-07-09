from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.modules.cart.schemas import CartItemUpsert, CartRead
from app.modules.cart.service import CartService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/cart", tags=["Cart"])


@router.post("/items")
def upsert_cart_item(
    payload: CartItemUpsert,
    x_guest_token: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
):
    # NOTE: registered-customer auth wiring follows the same require_roles() pattern as other
    # modules; omitted here so the guest path (X-Guest-Token) can be exercised without a token.
    cart = CartService(db).upsert_item(customer_id=None, guest_token=x_guest_token, payload=payload)
    return success_envelope(CartRead.model_validate(cart), "Cart updated successfully.")


# TODO: GET /cart, DELETE /cart/items/{id}, POST /cart/merge per API Specification §5.7.
