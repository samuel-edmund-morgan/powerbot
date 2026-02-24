#!/usr/bin/env python3
"""
Runtime smoke: resident UI must hide monetization controls when offers UI is not visible.

Runs inside app container (imports runtime modules) and validates:
- main menu has no "⭐ Партнер:" row when offers UI gate is false;
- notifications menu has no partner/digest toggles when offers UI gate is false;
- place detail keyboard for non-verified place has no paid CTA callbacks.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def _bootstrap_src_path() -> None:
    candidates: list[Path] = []

    env_src = os.getenv("APP_SRC_DIR", "").strip()
    if env_src:
        candidates.append(Path(env_src))

    cwd = Path.cwd()
    candidates.append(cwd / "src")
    candidates.append(Path("/app/src"))

    # When executed as a file (not stdin), keep local repo layout support.
    file_name = globals().get("__file__")
    if file_name:
        try:
            candidates.append(Path(file_name).resolve().parents[1] / "src")
        except Exception:
            pass

    seen: set[str] = set()
    for candidate in candidates:
        raw = str(candidate)
        if raw in seen:
            continue
        seen.add(raw)
        if candidate.exists() and raw not in sys.path:
            sys.path.insert(0, raw)


_bootstrap_src_path()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _flatten_button_texts(markup) -> list[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    out: list[str] = []
    for row in rows:
        for btn in row:
            out.append(str(getattr(btn, "text", "") or ""))
    return out


def _flatten_callback_data(markup) -> list[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    out: list[str] = []
    for row in rows:
        for btn in row:
            out.append(str(getattr(btn, "callback_data", "") or ""))
    return out


async def _run() -> None:
    import handlers

    original_gate = handlers._is_business_offers_ui_visible
    try:
        async def _gate_false() -> bool:
            return False

        handlers._is_business_offers_ui_visible = _gate_false  # type: ignore[assignment]

        chat_id = int(os.getenv("SMOKE_CHAT_ID", "7769511190"))

        main_kb = await handlers.get_main_keyboard_for_user(chat_id)
        main_texts = _flatten_button_texts(main_kb)
        _assert(
            not any(text.startswith("⭐ Партнер:") for text in main_texts),
            "main menu must not include sponsored partner row when offers gate is false",
        )

        notif_kb = await handlers.get_notifications_keyboard(chat_id)
        notif_texts = _flatten_button_texts(notif_kb)
        _assert(
            not any("Пропозиції партнерів" in text for text in notif_texts),
            "notifications menu must hide partner offers toggle when offers gate is false",
        )
        _assert(
            not any("Акції тижня" in text for text in notif_texts),
            "notifications menu must hide weekly digest toggle when offers gate is false",
        )

        place = {
            "id": 999001,
            "service_id": 101,
            "is_verified": 0,
            "verified_tier": "free",
            "contact_type": "call",
            "contact_value": "+380671112233",
            "link_url": "https://example.com",
            "logo_url": "https://example.com/logo.jpg",
            "promo_code": "TEST10",
            "menu_url": "https://example.com/menu",
            "order_url": "https://example.com/order",
        }
        place_kb = handlers.build_place_detail_keyboard(
            place,
            likes_count=0,
            user_liked=False,
            business_enabled=True,
            gallery_items=[],
        )
        place_callbacks = _flatten_callback_data(place_kb)
        forbidden_prefixes = (
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
        for prefix in forbidden_prefixes:
            _assert(
                not any(cb.startswith(prefix) for cb in place_callbacks),
                f"non-verified place card must not expose paid CTA `{prefix}`",
            )

        print("OK: no monetization when no verified runtime smoke passed.")
    finally:
        handlers._is_business_offers_ui_visible = original_gate  # type: ignore[assignment]


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
