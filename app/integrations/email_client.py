"""Wraps the transactional email provider. Never called directly from a router (SAD §5.2)."""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_email(*, to: str, subject: str, body: str) -> None:
    if not settings.EMAIL_PROVIDER_API_KEY:
        logger.info("EMAIL_PROVIDER_API_KEY not set; skipping send to %s: %s", to, subject)
        return
    # TODO: call the transactional email provider's API.
    raise NotImplementedError("Wire up the transactional email provider's SDK/HTTP client here.")
