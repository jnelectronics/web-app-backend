import uuid

from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.cart.models import Cart, CartItem


class CartRepository(BaseRepository[Cart]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Cart)

    def get_by_customer(self, customer_id: uuid.UUID) -> Cart | None:
        return self.db.query(Cart).filter(Cart.customer_id == customer_id, Cart.status == "active").first()

    def get_by_guest_token(self, guest_token: str) -> Cart | None:
        return self.db.query(Cart).filter(Cart.guest_token == guest_token, Cart.status == "active").first()

    def get_item(self, cart_id: uuid.UUID, variant_id: uuid.UUID) -> CartItem | None:
        return (
            self.db.query(CartItem)
            .filter(CartItem.cart_id == cart_id, CartItem.variant_id == variant_id)
            .first()
        )
