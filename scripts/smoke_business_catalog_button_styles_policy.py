#!/usr/bin/env python3
"""
Static smoke-check for resident catalog button style policy.

Policy:
- In resident category list (`cb_places_category`) we highlight only top paid slots:
  - Partner slot -> green (`STYLE_SUCCESS`)
  - Pro promo slot -> blue (`STYLE_PRIMARY`)
- Buttons with style are created via `ikb(...)` helper (safe fallback for older aiogram).
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    handlers = _read(root / "src/handlers.py")
    tg_buttons = _read(root / "src/tg_buttons.py")
    errors: list[str] = []

    # Handlers must use style constants and ikb helper.
    _must(handlers, "from tg_buttons import", errors=errors)
    _must(handlers, "STYLE_PRIMARY", errors=errors)
    _must(handlers, "STYLE_SUCCESS", errors=errors)
    _must(handlers, "ikb", errors=errors)
    _must(handlers, "btn_style: str | None = None", errors=errors)
    _must(handlers, "if int(place[\"id\"]) == partner_slot_id:", errors=errors)
    _must(handlers, "btn_style = STYLE_SUCCESS", errors=errors)
    _must(handlers, "elif int(place[\"id\"]) == promo_slot_id:", errors=errors)
    _must(handlers, "btn_style = STYLE_PRIMARY", errors=errors)
    _must(handlers, "btn = ikb(text=label, callback_data=cb, style=btn_style)", errors=errors)

    # Helper must keep style fallback behavior.
    _must(tg_buttons, "STYLE_DANGER", errors=errors)
    _must(tg_buttons, "STYLE_SUCCESS", errors=errors)
    _must(tg_buttons, "STYLE_PRIMARY", errors=errors)
    _must(tg_buttons, "kwargs[\"style\"] = normalized_style", errors=errors)
    _must(tg_buttons, "kwargs.pop(\"style\", None)", errors=errors)

    if errors:
        raise SystemExit(
            "ERROR: business catalog button styles policy violation(s):\n- "
            + "\n- ".join(errors)
        )

    print("OK: business catalog button styles policy smoke passed.")


if __name__ == "__main__":
    main()
