"""Telegram Stars payment provider helpers."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from .base import PaymentIntent


PAYLOAD_PREFIX = "bpayv1"
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60  # Telegram Stars monthly period (2592000)
SOURCE_TO_CODE = {
    "card": "c",
    "plans": "p",
}
CODE_TO_SOURCE = {
    "c": "card",
    "p": "plans",
}


@dataclass(frozen=True)
class TelegramStarsPayload:
    place_id: int
    tier: str
    external_payment_id: str
    tg_user_id: int
    source: str = "card"


def _source_to_code(source: str) -> str:
    normalized = str(source or "card").strip().lower()
    return SOURCE_TO_CODE.get(normalized, "c")


def _code_to_source(code: str) -> str:
    return CODE_TO_SOURCE.get(str(code or "").strip().lower(), "card")


def encode_telegram_stars_payload(
    *,
    place_id: int,
    tier: str,
    external_payment_id: str,
    tg_user_id: int,
    source: str,
) -> str:
    source_code = _source_to_code(source)
    return (
        f"{PAYLOAD_PREFIX}:{int(place_id)}:{str(tier).strip().lower()}:"
        f"{str(external_payment_id)}:{int(tg_user_id)}:{source_code}"
    )


def decode_telegram_stars_payload(raw_payload: str) -> TelegramStarsPayload | None:
    raw = str(raw_payload or "").strip()
    parts = raw.split(":")
    if len(parts) != 6:
        return None
    if parts[0] != PAYLOAD_PREFIX:
        return None

    try:
        place_id = int(parts[1])
        tg_user_id = int(parts[4])
    except Exception:
        return None
    if place_id <= 0 or tg_user_id <= 0:
        return None

    tier = str(parts[2]).strip().lower()
    external_payment_id = str(parts[3]).strip()
    source = _code_to_source(parts[5])
    if not tier or not external_payment_id:
        return None

    return TelegramStarsPayload(
        place_id=place_id,
        tier=tier,
        external_payment_id=external_payment_id,
        tg_user_id=tg_user_id,
        source=source,
    )


class TelegramStarsPaymentProvider:
    provider_name = "telegram_stars"

    def create_intent(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        amount_stars: int,
        source: str,
    ) -> PaymentIntent:
        external_payment_id = f"tg_{int(time.time())}_{secrets.token_hex(4)}"
        payload = encode_telegram_stars_payload(
            place_id=int(place_id),
            tier=str(tier).strip().lower(),
            external_payment_id=external_payment_id,
            tg_user_id=int(tg_user_id),
            source=str(source or "card"),
        )
        return PaymentIntent(
            provider=self.provider_name,
            place_id=int(place_id),
            tier=str(tier).strip().lower(),
            amount_stars=int(amount_stars),
            external_payment_id=external_payment_id,
            source=str(source or "card"),
            invoice_payload=payload,
        )
