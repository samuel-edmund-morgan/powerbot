#!/usr/bin/env python3
"""
Static smoke-check for business card activity stats block.

Policy:
- repository exposes `get_place_clicks_sum(...)`.
- business handlers have async `build_business_card_text(...)`.
- business card rendering includes both:
  - place views
  - coupon_open clicks
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, where: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{where}: missing `{token}`")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    repo_text = _read(root / "src/business/repository.py")
    handlers_text = _read(root / "src/business/handlers.py")
    errors: list[str] = []

    _must(repo_text, "async def get_place_clicks_sum(", where="src/business/repository.py", errors=errors)
    _must(repo_text, "FROM place_clicks_daily", where="src/business/repository.py", errors=errors)

    _must(handlers_text, "async def build_business_card_text(", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, 'action="coupon_open"', where="src/business/handlers.py", errors=errors)
    _must(handlers_text, 'action="chat"', where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "Перегляди картки", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "Відкриття промокоду", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "Відкриття чату", where="src/business/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business card activity stats policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business card activity stats policy smoke passed.")


if __name__ == "__main__":
    main()
