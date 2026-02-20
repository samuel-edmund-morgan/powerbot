#!/usr/bin/env python3
"""
Runtime smoke-check: resident Free card must not expose paid/contact CTA buttons.

Run in container:
  docker compose exec -T powerbot python - < scripts/smoke_business_free_card_no_paid_cta_runtime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "pcall_",
    "pchat_",
    "plink_",
    "plogo_",
    "pcoupon_",
    "pmenu_",
    "porder_",
    "pmimg1_",
    "pmimg2_",
    "pph1_",
    "pph2_",
    "pph3_",
)


def _flatten_callbacks(markup) -> list[str]:
    callbacks: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                callbacks.append(str(btn.callback_data))
    return callbacks


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _base_place() -> dict:
    return {
        "id": 101,
        "service_id": 7,
        "is_verified": False,
        "verified_tier": "free",
        "contact_type": "call",
        "contact_value": "+380671112233",
        "link_url": "https://example.com",
        "logo_url": "https://example.com/logo.png",
        "promo_code": "TEST10",
        "menu_url": "https://example.com/menu",
        "order_url": "https://example.com/order",
        "offer_1_image_url": "https://example.com/o1.png",
        "offer_2_image_url": "https://example.com/o2.png",
        "photo_1_url": "https://example.com/p1.png",
        "photo_2_url": "https://example.com/p2.png",
        "photo_3_url": "https://example.com/p3.png",
    }


def _assert_no_paid_cta(callbacks: list[str], *, case: str) -> None:
    offenders = [cb for cb in callbacks if cb.startswith(FORBIDDEN_PREFIXES)]
    _assert(not offenders, f"{case}: unexpected paid/contact callbacks in Free card: {offenders}")


def main() -> None:
    # Case 1: business mode enabled, but place is not verified -> no paid/contact CTAs.
    place_unverified = _base_place()
    kb_unverified = build_place_detail_keyboard(
        place_unverified,
        likes_count=3,
        user_liked=False,
        business_enabled=True,
    )
    callbacks_unverified = _flatten_callbacks(kb_unverified)
    _assert_no_paid_cta(callbacks_unverified, case="unverified")
    _assert(
        any(cb.startswith("like_") for cb in callbacks_unverified),
        "unverified: like button callback is missing",
    )
    _assert(
        any(cb.startswith("plrep_") for cb in callbacks_unverified),
        "unverified: suggest-fix button callback is missing",
    )

    # Case 2: business mode disabled -> even verified partner place must not show paid/contact CTAs.
    place_mode_off = _base_place()
    place_mode_off["is_verified"] = True
    place_mode_off["verified_tier"] = "partner"
    kb_mode_off = build_place_detail_keyboard(
        place_mode_off,
        likes_count=5,
        user_liked=True,
        business_enabled=False,
    )
    callbacks_mode_off = _flatten_callbacks(kb_mode_off)
    _assert_no_paid_cta(callbacks_mode_off, case="mode_off")
    _assert(
        any(cb.startswith("unlike_") for cb in callbacks_mode_off),
        "mode_off: unlike button callback is missing",
    )

    # Case 3: positive control — verified Light in business mode should expose at least one paid/contact CTA.
    place_light = _base_place()
    place_light["is_verified"] = True
    place_light["verified_tier"] = "light"
    place_light["contact_type"] = "chat"
    place_light["contact_value"] = "@my_business_chat"
    kb_light = build_place_detail_keyboard(
        place_light,
        likes_count=1,
        user_liked=False,
        business_enabled=True,
    )
    callbacks_light = _flatten_callbacks(kb_light)
    _assert(
        any(cb.startswith(("pchat_", "pcall_", "plink_", "plogo_", "pcoupon_")) for cb in callbacks_light),
        "light: expected at least one paid/contact CTA callback",
    )

    print("OK: business free-card no-paid-CTA runtime smoke passed.")


if __name__ == "__main__":
    try:
        repo_root = Path(__file__).resolve().parents[1]
    except Exception:
        repo_root = Path.cwd()
    sys.path.insert(0, str(repo_root / "src"))
    from handlers import build_place_detail_keyboard as _build  # noqa: WPS433

    build_place_detail_keyboard = _build
    main()
