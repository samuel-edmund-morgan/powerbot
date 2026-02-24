#!/usr/bin/env python3
"""
Policy/runtime-light smoke: no monetization hints when verified_count == 0.

This smoke is static (no aiogram import) to run on CI host python.
It verifies key resident UI gates in `src/handlers.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HANDLERS_FILE = REPO_ROOT / "src" / "handlers.py"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _extract_function_source(text: str, fn_name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == fn_name:
            return ast.get_source_segment(text, node) or ""
    return ""


def main() -> None:
    _assert(HANDLERS_FILE.exists(), f"file not found: {HANDLERS_FILE}")
    text = HANDLERS_FILE.read_text(encoding="utf-8")

    main_fn = _extract_function_source(text, "get_main_keyboard_for_user")
    _assert(main_fn, "missing get_main_keyboard_for_user")
    _assert(
        "if not await _is_business_offers_ui_visible():" in main_fn,
        "main menu must gate sponsored row via _is_business_offers_ui_visible()",
    )
    _assert(
        "⭐ Партнер:" in main_fn,
        "main menu function must contain sponsored row label contract",
    )

    notif_fn = _extract_function_source(text, "get_notifications_keyboard")
    _assert(notif_fn, "missing get_notifications_keyboard")
    _assert(
        "business_offers_visible = await _is_business_offers_ui_visible()" in notif_fn,
        "notifications menu must derive business_offers_visible from gate helper",
    )
    _assert(
        "if business_offers_visible:" in notif_fn,
        "notifications menu must conditionally render monetization toggles",
    )
    _assert(
        "Пропозиції партнерів" in notif_fn and "Акції тижня" in notif_fn,
        "notifications menu must include partner/digest labels behind gate",
    )

    places_fn = _extract_function_source(text, "cb_places_category")
    _assert(places_fn, "missing cb_places_category")
    _assert(
        "if business_enabled and has_verified" in places_fn,
        "catalog ranking hint/sort must be gated by has_verified",
    )
    _assert(
        "⭐ офіційний партнер • 🔝 промо • ✅ verified" in places_fn,
        "catalog ranking-hint copy contract missing",
    )

    detail_kb_fn = _extract_function_source(text, "build_place_detail_keyboard")
    _assert(detail_kb_fn, "missing build_place_detail_keyboard")
    _assert(
        "if business_enabled and place_enriched.get(\"is_verified\"):" in detail_kb_fn,
        "paid CTA buttons must be gated by place verified status",
    )

    print("OK: no monetization when no verified smoke passed.")


if __name__ == "__main__":
    main()
