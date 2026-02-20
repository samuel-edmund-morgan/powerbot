#!/usr/bin/env python3
"""
Dynamic smoke test: runtime style contract for business plan keyboard.

Checks:
- Light button stays default (no forced style)
- Pro button uses STYLE_PRIMARY
- Partner button uses STYLE_SUCCESS
- Cancel buttons use STYLE_DANGER for active/canceled paid subscriptions
"""

from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timedelta, timezone
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


def _find_by_callback(markup: Any, callback_data: str):
    for row in getattr(markup, "inline_keyboard", []):
        for button in row:
            if str(getattr(button, "callback_data", "")) == callback_data:
                return button
    return None


def _find_by_text(markup: Any, text_fragment: str):
    for row in getattr(markup, "inline_keyboard", []):
        for button in row:
            text = str(getattr(button, "text", ""))
            if text_fragment in text:
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

    from business.handlers import build_plan_keyboard  # noqa: WPS433
    from tg_buttons import STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS  # noqa: WPS433

    place_id = 321

    # Base case (free): verify plan colors.
    kb_free = build_plan_keyboard(
        place_id=place_id,
        current_tier="free",
        current_status="inactive",
        current_expires_at=None,
    )
    light_btn = _find_by_callback(kb_free, f"bp:{place_id}:light")
    pro_btn = _find_by_callback(kb_free, f"bp:{place_id}:pro")
    partner_btn = _find_by_callback(kb_free, f"bp:{place_id}:partner")
    _assert(light_btn is not None, "Light plan button not found")
    _assert(pro_btn is not None, "Pro plan button not found")
    _assert(partner_btn is not None, "Partner plan button not found")
    _assert(_style_of(light_btn) in (None, ""), f"Light button must be default style, got={_style_of(light_btn)}")
    _assert(_style_of(pro_btn) == STYLE_PRIMARY, f"Pro button style mismatch, got={_style_of(pro_btn)}")
    _assert(_style_of(partner_btn) == STYLE_SUCCESS, f"Partner button style mismatch, got={_style_of(partner_btn)}")

    # Active paid -> "Скасувати автопродовження" danger style.
    expires_future = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    kb_active_paid = build_plan_keyboard(
        place_id=place_id,
        current_tier="light",
        current_status="active",
        current_expires_at=expires_future,
    )
    cancel_btn = _find_by_text(kb_active_paid, "Скасувати автопродовження")
    _assert(cancel_btn is not None, "Cancel-autorenew button not found for active paid")
    _assert(_style_of(cancel_btn) == STYLE_DANGER, f"Cancel button style mismatch, got={_style_of(cancel_btn)}")

    # Canceled paid (still active by expires_at) -> red frozen cancel-state button.
    kb_canceled_paid = build_plan_keyboard(
        place_id=place_id,
        current_tier="light",
        current_status="canceled",
        current_expires_at=expires_future,
    )
    canceled_btn = _find_by_text(kb_canceled_paid, "Автопродовження скасовано")
    _assert(canceled_btn is not None, "Canceled-state button not found for canceled paid")
    _assert(_style_of(canceled_btn) == STYLE_DANGER, f"Canceled-state style mismatch, got={_style_of(canceled_btn)}")

    print("OK: business plan button runtime styles smoke passed.")


if __name__ == "__main__":
    main()

