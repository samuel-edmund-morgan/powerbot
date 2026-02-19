#!/usr/bin/env python3
"""
Smoke-check: resident place-card copy for Free-tier baseline.

Policy:
- Place detail card must show category line for residents:
  `🗂 Категорія: ...`
This preserves the Free-tier content contract: name + category + address.
"""

from __future__ import annotations

from pathlib import Path


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    handlers_file = repo_root / "src" / "handlers.py"
    text = handlers_file.read_text(encoding="utf-8")

    _assert(
        "🗂 <b>Категорія:</b>" in text,
        "Resident place card must include category line in _render_place_detail_message.",
    )

    print("OK: business free place-card content policy smoke passed.")


if __name__ == "__main__":
    main()
