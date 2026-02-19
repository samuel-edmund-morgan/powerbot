#!/usr/bin/env python3
"""
Static smoke-check for business reports priority moderation policy.

Policy:
- `list_place_reports()` joins subscriptions and computes `priority_score`.
- Pending reports are sorted by `priority_score DESC` before recency.
- Admin reports UI renders priority label.
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
    db_text = _read(root / "src/database.py")
    admin_text = _read(root / "src/admin/handlers.py")
    errors: list[str] = []

    _must(db_text, "LEFT JOIN business_subscriptions bs ON bs.place_id = pr.place_id", where="src/database.py", errors=errors)
    _must(db_text, "AS priority_score", where="src/database.py", errors=errors)
    _must(db_text, "ORDER BY priority_score DESC", where="src/database.py", errors=errors)
    _must(db_text, "\"priority_score\":", where="src/database.py", errors=errors)

    _must(admin_text, "def _report_priority_title(", where="src/admin/handlers.py", errors=errors)
    _must(admin_text, "Пріоритет:", where="src/admin/handlers.py", errors=errors)
    _must(admin_text, "_report_priority_title(item.get(\"priority_score\"))", where="src/admin/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business reports priority policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business reports priority policy smoke passed.")


if __name__ == "__main__":
    main()
