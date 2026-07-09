"""RQ job handlers for asynchronous payment reconciliation (SAD §10.6 retry policy)."""

import logging

logger = logging.getLogger(__name__)


def reconcile_pending_payment(payment_id: str) -> None:
    # TODO: poll the payment gateway for payments stuck in awaiting_payment beyond a timeout.
    logger.info("Reconciling pending payment: %s", payment_id)
