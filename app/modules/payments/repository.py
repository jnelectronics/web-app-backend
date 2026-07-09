from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.payments.models import Payment


class PaymentRepository(BaseRepository[Payment]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Payment)

    def get_by_provider_reference(self, provider_reference: str) -> Payment | None:
        return self.db.query(Payment).filter(Payment.provider_reference == provider_reference).first()

    def has_paid_payment(self, order_id) -> bool:
        return (
            self.db.query(Payment)
            .filter(Payment.order_id == order_id, Payment.status == "paid")
            .first()
            is not None
        )
