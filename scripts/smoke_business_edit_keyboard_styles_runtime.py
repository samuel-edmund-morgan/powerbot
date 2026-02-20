#!/usr/bin/env python3
"""
Dynamic smoke test: runtime button styles in business owner edit keyboard.

Checks:
- locked Premium fields use STYLE_PRIMARY when premium access is missing
- locked Partner fields use STYLE_SUCCESS when partner access is missing
- unlocked fields do not force style values
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any


def _resolve_repo_root() -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1])
    except Exception:
        pass
    candidates.extend([Path.cwd(), Path("/app"), Path("/workspace")])
    for root in candidates:
        if (root / "src").exists() and (root / "schema.sql").exists():
            return root
    raise FileNotFoundError("Cannot locate repo root with src/ and schema.sql")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _style_of(button: Any) -> str | None:
    value = getattr(button, "style", None)
    if value:
        return str(value)
    model_dump = getattr(button, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        raw = data.get("style")
        if raw:
            return str(raw)
    return None


def _button_by_callback(markup: Any, callback_data: str):
    for row in getattr(markup, "inline_keyboard", []):
        for button in row:
            if str(getattr(button, "callback_data", "")) == callback_data:
                return button
    return None


def main() -> None:
    root = _resolve_repo_root()
    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ.setdefault("BUSINESS_MODE", "1")
    os.environ.setdefault("BUSINESS_BOT_API_KEY", "smoke-business-token")

    sys.path.insert(0, str(root / "src"))

    from business.handlers import build_edit_fields_keyboard  # noqa: WPS433
    from tg_buttons import STYLE_PRIMARY, STYLE_SUCCESS  # noqa: WPS433

    place_id = 999
    locked_markup = build_edit_fields_keyboard(
        place_id,
        has_premium_access=False,
        has_partner_access=False,
    )

    premium_callbacks = [
        f"bef:{place_id}:menu_url",
        f"bef:{place_id}:order_url",
        f"bef:{place_id}:offer_1_text",
        f"bef:{place_id}:offer_2_text",
        f"bef:{place_id}:offer_1_image_url",
        f"bef:{place_id}:offer_2_image_url",
    ]
    partner_callbacks = [
        f"bef:{place_id}:photo_1_url",
        f"bef:{place_id}:photo_2_url",
        f"bef:{place_id}:photo_3_url",
    ]

    seen_styles: list[str] = []
    for cb in premium_callbacks:
        button = _button_by_callback(locked_markup, cb)
        _assert(button is not None, f"missing premium button callback: {cb}")
        style = _style_of(button)
        _assert(style == STYLE_PRIMARY, f"premium locked style mismatch for {cb}: got={style}")
        seen_styles.append(str(style))
    for cb in partner_callbacks:
        button = _button_by_callback(locked_markup, cb)
        _assert(button is not None, f"missing partner button callback: {cb}")
        style = _style_of(button)
        _assert(style == STYLE_SUCCESS, f"partner locked style mismatch for {cb}: got={style}")
        seen_styles.append(str(style))

    _assert(seen_styles, "no styled locked buttons found")

    unlocked_markup = build_edit_fields_keyboard(
        place_id,
        has_premium_access=True,
        has_partner_access=True,
    )
    # On unlocked fields we intentionally do not force styles.
    for cb in premium_callbacks + partner_callbacks:
        button = _button_by_callback(unlocked_markup, cb)
        _assert(button is not None, f"missing unlocked button callback: {cb}")
        style = _style_of(button)
        _assert(style in (None, ""), f"unlocked button must not force style for {cb}: got={style}")

    print("OK: business edit keyboard runtime styles smoke passed.")


if __name__ == "__main__":
    main()

