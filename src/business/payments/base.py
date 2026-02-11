"""Base contracts for business payment providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PaymentIntent:
    """Normalized payment intent used by UI handlers."""

    provider: str
    place_id: int
    tier: str
    amount_stars: int
    external_payment_id: str
    source: str = "card"
    invoice_payload: str | None = None


class PaymentProvider(Protocol):
    """Provider interface used by business billing flows."""

    provider_name: str

    def create_intent(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        amount_stars: int,
        source: str,
    ) -> PaymentIntent:
        """Create provider intent payload."""
