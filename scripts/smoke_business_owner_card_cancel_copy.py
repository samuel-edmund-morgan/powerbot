#!/usr/bin/env python3
"""
Runtime smoke: owner business-card copy for canceled subscription.

Contract:
- canceled paid subscription must render status `🔴 Скасована`
- expiration line (`Активно до`) must still be present (access until expires_at)
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path


def _resolve_repo_root() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1])
    except Exception:
        pass
    candidates.extend([Path.cwd(), Path("/app"), Path("/workspace")])
    for root in candidates:
        if (root / "src").exists() and (root / "schema.sql").exists():
            return root
    raise FileNotFoundError("Cannot resolve repository root")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _ensure_dotenv_stub() -> None:
    if "dotenv" in sys.modules:
        return
    try:
        import dotenv  # noqa: F401
        return
    except Exception:
        pass
    stub = types.ModuleType("dotenv")

    def _noop_load_dotenv(*_args, **_kwargs) -> bool:
        return False

    stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
    sys.modules["dotenv"] = stub


def main() -> None:
    repo_root = _resolve_repo_root()
    sys.path.insert(0, str(repo_root / "src"))

    _ensure_dotenv_stub()
    os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
    os.environ.setdefault("BUSINESS_MODE", "1")

    from business.handlers import format_business_card  # noqa: WPS433

    expires_at = "2030-03-01T12:30:00+00:00"
    item = {
        "place_name": "Smoke Place",
        "place_address": "Ньюкасл (24-в)",
        "place_description": "Test",
        "place_opening_hours": "",
        "place_contact_type": "",
        "place_contact_value": "",
        "place_link_url": "",
        "place_logo_url": "",
        "place_photo_1_url": "",
        "place_photo_2_url": "",
        "place_photo_3_url": "",
        "place_promo_code": "",
        "place_menu_url": "",
        "place_order_url": "",
        "place_offer_1_text": "",
        "place_offer_2_text": "",
        "place_offer_1_image_url": "",
        "place_offer_2_image_url": "",
        "ownership_status": "approved",
        "subscription_status": "canceled",
        "tier": "light",
        "is_verified": 1,
        "subscription_expires_at": expires_at,
    }

    text = format_business_card(item)
    _assert("📅 Статус підписки: 🔴 Скасована" in text, f"canceled status line missing:\n{text}")
    _assert(f"🕒 Активно до: {expires_at}" in text, f"expires line missing for canceled subscription:\n{text}")

    print("OK: business owner card canceled-copy smoke passed.")


if __name__ == "__main__":
    main()
