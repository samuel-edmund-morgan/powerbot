"""Shared plan matrix for business subscriptions."""

from __future__ import annotations

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
