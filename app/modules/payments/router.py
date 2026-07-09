from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.modules.payments.schemas import PaymentInitiate, PaymentRead, PaymentWebhookPayload
from app.modules.payments.service import PaymentService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("", status_code=status.HTTP_201_CREATED)
def initiate_payment(payload: PaymentInitiate, db: Session = Depends(get_db_session)):
    payment = PaymentService(db).initiate(payload)
    return success_envelope(PaymentRead.model_validate(payment), "Payment initiated successfully.")


@router.post("/webhook")
def payment_webhook(payload: PaymentWebhookPayload, db: Session = Depends(get_db_session)):
    payment = PaymentService(db).handle_webhook(payload)
    return success_envelope(PaymentRead.model_validate(payment), "Webhook processed successfully.")


# TODO: GET /payments/{id}, GET /orders/{id}/payments per API Specification §5.9.
