#!/usr/bin/env python3
"""
Static smoke-check: WebApp partner-offers toggles contract.

Policy:
- WebApp notifications view must include toggle `sponsoredToggle`.
- WebApp notifications view must include toggle `offersDigestToggle`.
- Frontend render must hide/show monetization toggles by `settings.business_offers_visible`.
- Frontend save must send monetization toggles only when `business_offers_visible=true`.
- Backend must sanitize notification settings for UI via `business_offers_visible`.
- Backend notifications API must persist monetization toggles only when business offers are visible.
- Shared notification settings must include `sponsored_offers_enabled`.
- Shared notification settings must include `offers_digest_enabled`.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str], where: str) -> None:
    if token not in text:
        errors.append(f"{where}: missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    api_server = _read(root / "src" / "api_server.py")
    database = _read(root / "src" / "database.py")
    webapp_index = _read(root / "webapp" / "index.html")
    webapp_state = _read(root / "webapp" / "state.js")
    webapp_ui = _read(root / "webapp" / "ui.js")
    webapp_app = _read(root / "webapp" / "app.js")
    handlers = _read(root / "src" / "handlers.py")

    _must(webapp_index, 'id="sponsoredToggle"', errors=errors, where="webapp/index.html")
    _must(webapp_index, 'id="offersDigestToggle"', errors=errors, where="webapp/index.html")
    _must(
        webapp_state,
        'sponsoredToggle: document.getElementById("sponsoredToggle")',
        errors=errors,
        where="webapp/state.js",
    )
    _must(
        webapp_state,
        'offersDigestToggle: document.getElementById("offersDigestToggle")',
        errors=errors,
        where="webapp/state.js",
    )
    _must(
        webapp_ui,
        "const businessOffersVisible = settings.business_offers_visible === true",
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_ui,
        "elements.sponsoredToggle.checked = settings.sponsored_offers_enabled === true",
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_ui,
        'elements.sponsoredToggle.closest("label.toggle")',
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_ui,
        "elements.offersDigestToggle.checked = settings.offers_digest_enabled === true",
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_ui,
        'elements.offersDigestToggle.closest("label.toggle")',
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_app,
        "state.settings?.business_offers_visible === true",
        errors=errors,
        where="webapp/app.js",
    )
    _must(
        webapp_app,
        "payload.sponsored_offers_enabled = elements.sponsoredToggle?.checked ?? false",
        errors=errors,
        where="webapp/app.js",
    )
    _must(
        webapp_app,
        "payload.offers_digest_enabled = elements.offersDigestToggle?.checked ?? false",
        errors=errors,
        where="webapp/app.js",
    )

    _must(api_server, "set_sponsored_offers_enabled", errors=errors, where="src/api_server.py")
    _must(
        api_server,
        "has_any_published_verified_business_place",
        errors=errors,
        where="src/api_server.py",
    )
    _must(
        api_server,
        "def _sanitize_notification_settings_for_ui(",
        errors=errors,
        where="src/api_server.py",
    )
    _must(
        api_server,
        "business_offers_visible = await _is_business_offers_ui_visible()",
        errors=errors,
        where="src/api_server.py",
    )
    _must(
        api_server,
        'if business_offers_visible and "sponsored_offers_enabled" in data:',
        errors=errors,
        where="src/api_server.py",
    )
    _must(api_server, "set_offers_digest_enabled", errors=errors, where="src/api_server.py")
    _must(
        api_server,
        'if business_offers_visible and "offers_digest_enabled" in data:',
        errors=errors,
        where="src/api_server.py",
    )
    _must(
        api_server,
        "_sanitize_notification_settings_for_ui(settings, business_offers_visible)",
        errors=errors,
        where="src/api_server.py",
    )

    _must(database, "def sponsored_offers_enabled_key(chat_id: int) -> str:", errors=errors, where="src/database.py")
    _must(database, "async def get_sponsored_offers_enabled(chat_id: int) -> bool:", errors=errors, where="src/database.py")
    _must(database, "async def set_sponsored_offers_enabled(chat_id: int, enabled: bool) -> None:", errors=errors, where="src/database.py")
    _must(database, "def offers_digest_enabled_key(chat_id: int) -> str:", errors=errors, where="src/database.py")
    _must(database, "async def get_offers_digest_enabled(chat_id: int) -> bool:", errors=errors, where="src/database.py")
    _must(database, "async def set_offers_digest_enabled(chat_id: int, enabled: bool) -> None:", errors=errors, where="src/database.py")
    _must(database, '"sponsored_offers_enabled": sponsored_enabled,', errors=errors, where="src/database.py")
    _must(database, '"offers_digest_enabled": offers_digest_enabled,', errors=errors, where="src/database.py")

    _must(handlers, "notif_toggle_offers_digest", errors=errors, where="src/handlers.py")
    _must(handlers, "📬 Акції тижня:", errors=errors, where="src/handlers.py")
    _must(handlers, "cb_toggle_offers_digest", errors=errors, where="src/handlers.py")

    if errors:
        raise SystemExit(
            "ERROR: business webapp partner-offers toggles policy violation(s):\n- "
            + "\n- ".join(errors)
        )

    print("OK: business webapp partner-offers toggles policy smoke passed.")


if __name__ == "__main__":
    main()
