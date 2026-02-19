#!/usr/bin/env python3
"""
Smoke-check: "Пошук закладу" routing must not be shadowed by generic fallback.

Policy:
- Generic private-text fallback (`reply_keyboard_regex_fallback`) must include
  guard filter `message.chat.id not in search_waiting_users`.
- Search query handler (`handle_search_query`) must exist.
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
        "@router.message(StateFilter(None), F.text, lambda message: message.chat.id not in search_waiting_users)"
        in text,
        "Generic text fallback must skip users in search_waiting_users.",
    )
    _assert(
        "async def handle_search_query(message: Message):" in text,
        "Search query handler is missing.",
    )

    print("OK: search menu routing policy smoke passed.")


if __name__ == "__main__":
    main()
