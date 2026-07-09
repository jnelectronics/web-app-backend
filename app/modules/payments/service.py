from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import DuplicatePaymentError, NotFoundError
from app.db.enums import PaymentStatus
from app.modules.orders.models import Order
from app.modules.payments.models import Payment
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.schemas import PaymentInitiate, PaymentWebhookPayload


class PaymentService:
    """FR-PAY-001-010: webhook confirmation is idempotent by provider_reference (API Spec §7)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = PaymentRepository(db)

    def initiate(self, payload: PaymentInitiate) -> Payment:
        payment = Payment(
            order_id=payload.order_id,
            provider=payload.provider,
            amount=payload.amount,
            status=PaymentStatus.AWAITING_PAYMENT,
            initiated_at=datetime.now(timezone.utc),
        )
        return self.repository.create(payment)

    def handle_webhook(self, payload: PaymentWebhookPayload) -> Payment:
        payment = self.repository.get_by_provider_reference(payload.provider_reference)
        if not payment:
            raise NotFoundError("No payment found for this provider reference.")

        if payment.status == PaymentStatus.PAID:
            return payment  # idempotent no-op, already processed

        if payload.status == PaymentStatus.PAID and self.repository.has_paid_payment(payment.order_id):
            raise DuplicatePaymentError("A successful payment already exists for this order.")

        try:
            payment.status = payload.status
            payment.completed_at = datetime.now(timezone.utc)
            if payload.status == PaymentStatus.PAID:
                order = self.db.get(Order, payment.order_id)
                if order and order.status.value == "pending":
                    order.status = "confirmed"
            self.db.commit()
            self.db.refresh(payment)
            return payment
        except Exception:
            self.db.rollback()
            raise
