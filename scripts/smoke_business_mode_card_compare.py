#!/usr/bin/env python3
"""
Smoke-check: resident place-card CTA behavior for BUSINESS_MODE OFF vs ON.

Policy:
- If `business_enabled=False`, paid/verified CTAs must stay hidden even when
  place row already has verified metadata.
- If `business_enabled=True`, the same verified place must expose paid CTAs
  according to entitlement fields.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _setup_import_path() -> None:
    for candidate in (Path.cwd() / "src", Path("/app/src")):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()

from handlers import build_place_detail_keyboard  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _collect_callbacks(kb) -> list[str]:
    callbacks: list[str] = []
    for row in kb.inline_keyboard:
        for btn in row:
            cb = getattr(btn, "callback_data", None)
            if cb:
                callbacks.append(str(cb))
    return callbacks


def _count_prefix(callbacks: list[str], prefix: str) -> int:
    return sum(1 for cb in callbacks if cb.startswith(prefix))


def main() -> None:
    verified_place = {
        "id": 501,
        "service_id": 9,
        "is_verified": 1,
        "verified_tier": "pro",
        "contact_type": "chat",
        "contact_value": "@smoke_owner",
        "link_url": "https://example.org/link",
        "logo_url": "https://example.org/logo.jpg",
        "promo_code": "SMOKE500",
        "menu_url": "https://example.org/menu",
        "order_url": "https://example.org/order",
        "offer_1_image_url": "https://example.org/offer1.jpg",
        "offer_2_image_url": "https://example.org/offer2.jpg",
        "photo_1_url": "",
        "photo_2_url": "",
        "photo_3_url": "",
    }

    # OFF: no business CTAs even if place is verified in DB metadata.
    kb_off = build_place_detail_keyboard(
        dict(verified_place),
        likes_count=7,
        user_liked=False,
        business_enabled=False,
    )
    cbs_off = _collect_callbacks(kb_off)
    for prefix in (
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
    ):
        _assert(_count_prefix(cbs_off, prefix) == 0, f"BUSINESS_MODE=0 must hide {prefix} CTA")
    _assert(_count_prefix(cbs_off, "plrep_") == 1, "report CTA must stay visible in BUSINESS_MODE=0")
    _assert(_count_prefix(cbs_off, "places_cat_") == 1, "back CTA must stay visible in BUSINESS_MODE=0")

    # ON: same place exposes verified/pro CTAs.
    kb_on = build_place_detail_keyboard(
        dict(verified_place),
        likes_count=7,
        user_liked=False,
        business_enabled=True,
    )
    cbs_on = _collect_callbacks(kb_on)
    _assert(_count_prefix(cbs_on, "pchat_") == 1, "BUSINESS_MODE=1 must expose chat CTA for verified place")
    _assert(_count_prefix(cbs_on, "plink_") == 1, "BUSINESS_MODE=1 must expose link CTA for verified place")
    _assert(_count_prefix(cbs_on, "plogo_") == 1, "BUSINESS_MODE=1 must expose logo CTA for verified place")
    _assert(_count_prefix(cbs_on, "pcoupon_") == 1, "BUSINESS_MODE=1 must expose promo CTA for verified place")
    _assert(_count_prefix(cbs_on, "pmenu_") == 1, "BUSINESS_MODE=1 must expose menu CTA for pro tier")
    _assert(_count_prefix(cbs_on, "porder_") == 1, "BUSINESS_MODE=1 must expose order CTA for pro tier")

    print("OK: business mode place-card OFF/ON compare smoke passed.")


if __name__ == "__main__":
    main()
