#!/usr/bin/env python3
"""
Static smoke-check for Premium daily activity stats in business card.

Policy:
- repository provides `get_place_activity_daily(...)` using daily tables.
- business card builder renders daily block only for Premium/Partner entitlement.
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

    _must(repo_text, "async def get_place_activity_daily(", where="src/business/repository.py", errors=errors)
    _must(repo_text, "FROM place_views_daily", where="src/business/repository.py", errors=errors)
    _must(repo_text, "FROM place_clicks_daily", where="src/business/repository.py", errors=errors)
    _must(repo_text, "GROUP BY day", where="src/business/repository.py", errors=errors)

    _must(handlers_text, "_has_active_premium_subscription(item)", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "get_place_activity_daily(place_id, days=7)", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "📈 По днях (7 днів)", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "👁", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "🎯", where="src/business/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business daily-stats policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business daily stats policy smoke passed.")


if __name__ == "__main__":
    main()
