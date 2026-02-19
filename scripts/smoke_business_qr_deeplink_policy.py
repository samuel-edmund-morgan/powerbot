#!/usr/bin/env python3
"""
Static smoke-check: businessbot QR deep-link policy.

Policy:
- owner card has QR button callback `bqr:<place_id>` (with lock variant for free tier)
- callback handler exists and validates paid entitlement
- deep-link format uses resident bot `?start=place_<id>`
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"missing token `{token}`")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    text = _read(root / "src/business/handlers.py")
    errors: list[str] = []

    _must(text, 'CB_QR_OPEN_PREFIX = "bqr:"', errors=errors)
    _must(text, 'callback_data=f"{CB_QR_OPEN_PREFIX}{place_id}"', errors=errors)
    _must(text, '@router.callback_query(F.data.startswith(CB_QR_OPEN_PREFIX))', errors=errors)
    _must(text, 'if not _has_active_paid_subscription(item):', errors=errors)
    _must(text, 'f"https://t.me/{bot_username}?start=place_{int(place_id)}"', errors=errors)
    _must(text, 'create-qr-code/?size=600x600&data=', errors=errors)
    _must(text, 'InlineKeyboardButton(text="🔳 Відкрити QR", url=qr_url)', errors=errors)

    if errors:
        raise SystemExit("ERROR: business QR deep-link policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business QR deep-link policy smoke passed.")


if __name__ == "__main__":
    main()

