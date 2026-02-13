"""Background maintenance tasks for business module."""

from __future__ import annotations

import asyncio
import logging

from business.guards import is_business_feature_enabled
from business.service import BusinessCabinetService


logger = logging.getLogger(__name__)

SUBSCRIPTION_RECONCILE_INTERVAL_SEC = 300


async def subscription_maintenance_loop(*, interval_sec: int = SUBSCRIPTION_RECONCILE_INTERVAL_SEC) -> None:
    """Periodically reconcile paid subscription lifecycle states."""
    if not is_business_feature_enabled():
        logger.info("Business subscription maintenance disabled (BUSINESS_MODE=0).")
        return

    service = BusinessCabinetService()
    sleep_for = max(30, int(interval_sec))

    while True:
        try:
            stats = await service.reconcile_subscription_states()
            if int(stats.get("total_changed") or 0) > 0:
                logger.info(
                    "Business subscriptions reconciled: scanned=%s active->past_due=%s past_due->free=%s",
                    stats.get("scanned"),
                    stats.get("changed_active_to_past_due"),
                    stats.get("changed_past_due_to_free"),
                )
        except Exception:
            logger.exception("Business subscription maintenance loop failed")
        await asyncio.sleep(sleep_for)
