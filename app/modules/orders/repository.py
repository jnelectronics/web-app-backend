from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.orders.models import Order, OrderStatusHistory


class OrderRepository(BaseRepository[Order]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Order)

    def record_status_change(self, entry: OrderStatusHistory) -> None:
        self.db.add(entry)
