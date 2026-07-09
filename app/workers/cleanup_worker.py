"""RQ job handlers for periodic housekeeping (stale guest carts, expired refresh tokens)."""

import logging

logger = logging.getLogger(__name__)


def purge_abandoned_carts() -> None:
    # TODO: mark guest carts with no activity beyond a retention window as 'abandoned'.
    logger.info("Purging abandoned carts.")


def purge_expired_refresh_tokens() -> None:
    # TODO: hard-delete refresh_tokens rows past their expires_at (not business data, safe to prune).
    logger.info("Purging expired refresh tokens.")
