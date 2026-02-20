#!/usr/bin/env python3
"""
Static smoke-check: WebApp sponsored-offers toggle contract.

Policy:
- WebApp notifications view must include toggle `sponsoredToggle`.
- Frontend state/render/save flow must read/write `sponsored_offers_enabled`.
- Backend notifications API must accept and persist `sponsored_offers_enabled`.
- Shared notification settings must include `sponsored_offers_enabled`.
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

    _must(webapp_index, 'id="sponsoredToggle"', errors=errors, where="webapp/index.html")
    _must(
        webapp_state,
        'sponsoredToggle: document.getElementById("sponsoredToggle")',
        errors=errors,
        where="webapp/state.js",
    )
    _must(
        webapp_ui,
        "elements.sponsoredToggle.checked = settings.sponsored_offers_enabled !== false",
        errors=errors,
        where="webapp/ui.js",
    )
    _must(
        webapp_app,
        "sponsored_offers_enabled: elements.sponsoredToggle?.checked ?? true",
        errors=errors,
        where="webapp/app.js",
    )

    _must(api_server, "set_sponsored_offers_enabled", errors=errors, where="src/api_server.py")
    _must(
        api_server,
        'if "sponsored_offers_enabled" in data:',
        errors=errors,
        where="src/api_server.py",
    )
    _must(
        api_server,
        'await set_sponsored_offers_enabled(user_id, bool(data["sponsored_offers_enabled"]))',
        errors=errors,
        where="src/api_server.py",
    )

    _must(database, "def sponsored_offers_enabled_key(chat_id: int) -> str:", errors=errors, where="src/database.py")
    _must(database, "async def get_sponsored_offers_enabled(chat_id: int) -> bool:", errors=errors, where="src/database.py")
    _must(database, "async def set_sponsored_offers_enabled(chat_id: int, enabled: bool) -> None:", errors=errors, where="src/database.py")
    _must(database, '"sponsored_offers_enabled": sponsored_enabled,', errors=errors, where="src/database.py")

    if errors:
        raise SystemExit(
            "ERROR: business webapp sponsored-toggle policy violation(s):\n- "
            + "\n- ".join(errors)
        )

    print("OK: business webapp sponsored-toggle policy smoke passed.")


if __name__ == "__main__":
    main()
