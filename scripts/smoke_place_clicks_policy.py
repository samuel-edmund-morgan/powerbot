#!/usr/bin/env python3
"""
Static smoke-check for place clicks analytics policy.

Policy:
- DB has daily click aggregation table `place_clicks_daily`.
- `record_place_click()` exists and upserts daily counters by action.
- Resident place UI exposes paid CTA callbacks and records click actions.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must_have(text: str, token: str, *, file_label: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{file_label}: missing `{token}`")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db_text = _read(root / "src/database.py")
    handlers_text = _read(root / "src/handlers.py")

    errors: list[str] = []

    _must_have(db_text, "CREATE TABLE IF NOT EXISTS place_clicks_daily", file_label="src/database.py", errors=errors)
    _must_have(db_text, "PRIMARY KEY (place_id, day, action)", file_label="src/database.py", errors=errors)
    _must_have(db_text, "async def record_place_click(", file_label="src/database.py", errors=errors)
    _must_have(db_text, "ON CONFLICT(place_id, day, action) DO UPDATE SET cnt = cnt + 1", file_label="src/database.py", errors=errors)

    _must_have(handlers_text, 'callback_data=f"pcoupon_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pcoupon_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "coupon_open")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"pchat_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pchat_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "chat")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"pcall_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pcall_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "call")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"plink_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("plink_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "link")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"pmenu_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pmenu_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "menu")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"porder_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("porder_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "order")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"pmimg1_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pmimg1_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "offer1_image")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'callback_data=f"pmimg2_{place_id}"', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'F.data.startswith("pmimg2_")', file_label="src/handlers.py", errors=errors)
    _must_have(handlers_text, 'await record_place_click(place_id, "offer2_image")', file_label="src/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: place-clicks policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: place clicks policy smoke passed.")


if __name__ == "__main__":
    main()
