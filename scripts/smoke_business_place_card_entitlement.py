#!/usr/bin/env python3
"""
Smoke-check: resident place-card entitlement keyboard contract.

Policy:
- Free (not verified): no paid CTA callbacks on card.
- Verified: at most one contact CTA (pcall_ or pchat_), never both.
- Promo CTA is available only for verified places with non-empty promo_code.
- Premium/Partner: extra CTAs `pmenu_` and `porder_` are available when URLs are set.
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
            if getattr(btn, "callback_data", None):
                callbacks.append(str(btn.callback_data))
    return callbacks


def _count_prefix(callbacks: list[str], prefix: str) -> int:
    return sum(1 for cb in callbacks if cb.startswith(prefix))


def main() -> None:
    base = {
        "id": 101,
        "service_id": 7,
        "is_verified": 0,
        "contact_type": "",
        "contact_value": "",
        "link_url": "",
        "promo_code": "",
        "menu_url": "",
        "order_url": "",
    }

    # Free place: no paid CTA callbacks.
    kb_free = build_place_detail_keyboard(
        dict(base),
        likes_count=3,
        user_liked=False,
        business_enabled=True,
    )
    cbs_free = _collect_callbacks(kb_free)
    _assert(_count_prefix(cbs_free, "pcall_") == 0, "free must not expose call CTA")
    _assert(_count_prefix(cbs_free, "pchat_") == 0, "free must not expose chat CTA")
    _assert(_count_prefix(cbs_free, "plink_") == 0, "free must not expose link CTA")
    _assert(_count_prefix(cbs_free, "pcoupon_") == 0, "free must not expose promo CTA")
    _assert(_count_prefix(cbs_free, "pmenu_") == 0, "free must not expose menu CTA")
    _assert(_count_prefix(cbs_free, "porder_") == 0, "free must not expose order CTA")
    _assert(_count_prefix(cbs_free, "plrep_") == 1, "report CTA must stay visible on free")

    # Verified light with chat contact + link + promo.
    verified_chat = dict(base)
    verified_chat.update(
        {
            "id": 102,
            "is_verified": 1,
            "verified_tier": "light",
            "contact_type": "chat",
            "contact_value": "@light_chat",
            "link_url": "https://example.org/menu",
            "promo_code": "LIGHT100",
        }
    )
    kb_light_chat = build_place_detail_keyboard(
        verified_chat,
        likes_count=10,
        user_liked=True,
        business_enabled=True,
    )
    cbs_light_chat = _collect_callbacks(kb_light_chat)
    contact_count_chat = _count_prefix(cbs_light_chat, "pcall_") + _count_prefix(cbs_light_chat, "pchat_")
    _assert(contact_count_chat == 1, "verified light(chat) must expose exactly one contact CTA")
    _assert(_count_prefix(cbs_light_chat, "pchat_") == 1, "verified light(chat) must expose chat CTA")
    _assert(_count_prefix(cbs_light_chat, "pcall_") == 0, "verified light(chat) must not expose call CTA")
    _assert(_count_prefix(cbs_light_chat, "plink_") == 1, "verified with link_url should expose link CTA")
    _assert(_count_prefix(cbs_light_chat, "pcoupon_") == 1, "verified with promo_code should expose promo CTA")
    _assert(_count_prefix(cbs_light_chat, "pmenu_") == 0, "verified light must not expose menu CTA")
    _assert(_count_prefix(cbs_light_chat, "porder_") == 0, "verified light must not expose order CTA")

    # Verified light with call contact and empty promo.
    verified_call = dict(base)
    verified_call.update(
        {
            "id": 103,
            "is_verified": 1,
            "verified_tier": "light",
            "contact_type": "call",
            "contact_value": "+380671112233",
            "link_url": "",
            "promo_code": "",
        }
    )
    kb_light_call = build_place_detail_keyboard(
        verified_call,
        likes_count=2,
        user_liked=False,
        business_enabled=True,
    )
    cbs_light_call = _collect_callbacks(kb_light_call)
    contact_count_call = _count_prefix(cbs_light_call, "pcall_") + _count_prefix(cbs_light_call, "pchat_")
    _assert(contact_count_call == 1, "verified light(call) must expose exactly one contact CTA")
    _assert(_count_prefix(cbs_light_call, "pcall_") == 1, "verified light(call) must expose call CTA")
    _assert(_count_prefix(cbs_light_call, "pchat_") == 0, "verified light(call) must not expose chat CTA")
    _assert(_count_prefix(cbs_light_call, "pcoupon_") == 0, "verified without promo must not expose promo CTA")
    _assert(_count_prefix(cbs_light_call, "pmenu_") == 0, "verified light must not expose menu CTA")
    _assert(_count_prefix(cbs_light_call, "porder_") == 0, "verified light must not expose order CTA")

    # Verified premium with menu/order URLs.
    verified_pro = dict(base)
    verified_pro.update(
        {
            "id": 104,
            "is_verified": 1,
            "verified_tier": "pro",
            "contact_type": "chat",
            "contact_value": "@pro_chat",
            "link_url": "https://example.org/site",
            "promo_code": "PRO500",
            "menu_url": "https://example.org/menu",
            "order_url": "https://example.org/order",
        }
    )
    kb_pro = build_place_detail_keyboard(
        verified_pro,
        likes_count=12,
        user_liked=False,
        business_enabled=True,
    )
    cbs_pro = _collect_callbacks(kb_pro)
    _assert(_count_prefix(cbs_pro, "pmenu_") == 1, "verified pro with menu_url must expose menu CTA")
    _assert(_count_prefix(cbs_pro, "porder_") == 1, "verified pro with order_url must expose order CTA")

    print("OK: business place-card entitlement smoke passed.")


if __name__ == "__main__":
    main()
