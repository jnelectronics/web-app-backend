"""Wraps the payment gateway (mobile money / card). Webhook verification lives here too."""

from app.core.config import settings


def initiate_payment(*, order_id: str, amount: float, provider: str) -> dict:
    if not settings.PAYMENT_PROVIDER_API_KEY:
        raise RuntimeError("PAYMENT_PROVIDER_API_KEY is not configured.")
    # TODO: call the payment gateway's API to start a charge/collection request.
    raise NotImplementedError("Wire up the payment gateway's SDK/HTTP client here.")


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    # TODO: verify the gateway's webhook signature before trusting payload contents.
    raise NotImplementedError("Wire up the payment gateway's webhook signature verification here.")
