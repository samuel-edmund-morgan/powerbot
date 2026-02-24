"""Shared plan matrix for business subscriptions."""

from __future__ import annotations

import os
from typing import Final


SUPPORTED_TIERS: Final[set[str]] = {"free", "light", "pro", "partner"}
PAID_TIERS: Final[set[str]] = {"light", "pro", "partner"}

# Keep DB tier values stable (`pro` in DB), but show "Premium" in UI.
PLAN_TITLES: Final[dict[str, str]] = {
    "free": "Free",
    "light": "Light",
    "pro": "Premium",
    "partner": "Partner",
}

# Monthly prices in Telegram Stars.
PLAN_STARS_PRICES: Final[dict[str, int]] = {
    "light": 1000,
    "pro": 2500,
    "partner": 5000,
}

_TEST_PRICE_OVERRIDE_ENV: Final[dict[str, str]] = {
    "light": "BUSINESS_TEST_STARS_PRICE_LIGHT",
    "pro": "BUSINESS_TEST_STARS_PRICE_PRO",
    "partner": "BUSINESS_TEST_STARS_PRICE_PARTNER",
}


def _parse_non_negative_int(raw: str | None) -> int | None:
    value = str(raw or "").strip().strip('"').strip("'")
    if not value:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed < 0:
        return None
    return parsed


def get_effective_plan_stars_prices() -> dict[str, int]:
    """Return runtime prices with optional test-only env overrides."""
    prices = dict(PLAN_STARS_PRICES)
    for tier, env_key in _TEST_PRICE_OVERRIDE_ENV.items():
        override = _parse_non_negative_int(os.getenv(env_key))
        if override is not None:
            prices[tier] = int(override)
    return prices


def get_plan_stars_price(tier: str) -> int:
    normalized = str(tier or "").strip().lower()
    return int(get_effective_plan_stars_prices().get(normalized, 0))
