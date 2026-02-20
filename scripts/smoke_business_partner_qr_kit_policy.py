#!/usr/bin/env python3
"""
Static smoke-check: Partner QR-kit contract.

Policy:
- Business owner card shows `QR-комплект` action.
- Free/Light/Pro owners see locked Partner CTA.
- Partner owners can open QR-kit with PNG templates + instructions.
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
    handlers = _read(root / "src" / "business" / "handlers.py")
    errors: list[str] = []

    _must(handlers, 'CB_QR_KIT_OPEN_PREFIX = "bqrkit:"', errors=errors)
    _must(handlers, "def _resident_place_qr_kit_png_url(", errors=errors)
    _must(handlers, "quickchart.io/qr", errors=errors)
    _must(handlers, "qr_kit_text = \"🪧 QR-комплект\"", errors=errors)
    _must(handlers, "🔒 QR-комплект (", errors=errors)
    _must(handlers, "_has_active_partner_subscription(item)", errors=errors)
    _must(handlers, "@router.callback_query(F.data.startswith(CB_QR_KIT_OPEN_PREFIX))", errors=errors)
    _must(handlers, "async def cb_open_place_qr_kit(", errors=errors)
    _must(handlers, "🖼 PNG • Вхід", errors=errors)
    _must(handlers, "🖼 PNG • Каса", errors=errors)
    _must(handlers, "🖼 PNG • Столик", errors=errors)
    _must(handlers, "🔒 QR-комплект доступний з активним тарифом", errors=errors)

    if errors:
        raise SystemExit(
            "ERROR: business partner QR-kit policy violation(s):\n- "
            + "\n- ".join(errors)
        )

    print("OK: business partner QR-kit policy smoke passed.")


if __name__ == "__main__":
    main()
