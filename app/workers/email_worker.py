"""RQ job handlers for transactional email (enqueued from services, never from routers)."""

import logging

from app.integrations.email_client import send_email

logger = logging.getLogger(__name__)


def send_order_confirmation_email(order_id: str, to: str) -> None:
    send_email(to=to, subject=f"Order confirmation: {order_id}", body="Thank you for your order.")


def notify_staff_new_order(order_id: str) -> None:
    logger.info("New order placed: %s", order_id)
    # TODO: notify sales attendants for the fulfilling branch.


def send_password_reset_email(to: str, reset_token: str) -> None:
    send_email(to=to, subject="Password reset request", body=f"Reset token: {reset_token}")
