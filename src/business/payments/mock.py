"""Mock payment provider for test business bot."""

from __future__ import annotations

import secrets
import time

from .base import PaymentIntent


class MockPaymentProvider:
    provider_name = "mock"

    def create_intent(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        amount_stars: int,
        source: str,
    ) -> PaymentIntent:
        del tg_user_id  # Not needed for mock provider ids.
        external_payment_id = f"mock_{int(time.time())}_{secrets.token_hex(4)}"
        return PaymentIntent(
            provider=self.provider_name,
            place_id=int(place_id),
            tier=str(tier).strip().lower(),
            amount_stars=int(amount_stars),
            external_payment_id=external_payment_id,
            source=str(source or "card"),
            invoice_payload=None,
        )
