"""RQ job handlers for time-boxed promotions (banners, product discounts)."""

import logging

logger = logging.getLogger(__name__)


def deactivate_expired_promotions() -> None:
    # TODO: flip is_active=false on banners/product_discounts whose ends_at has passed.
    logger.info("Checking for expired promotions.")
