#!/usr/bin/env python3
"""
Static policy smoke for adbot source-chat filter logging.

Ensures adbot runtime writes a structured decision reason when a message
is ignored due to source-chat allowlist filtering.
"""

from __future__ import annotations

from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "src" / "adbot_main.py"
    text = target.read_text(encoding="utf-8")

    condition = "if source_chat_ids and event.chat_id not in source_chat_ids:"
    _assert(condition in text, "source allowlist condition missing in adbot_main.py")

    reason = "source_chat_not_allowed"
    _assert(reason in text, "missing source filter decision reason token")
    _assert("build_decision_payload(" in text, "build_decision_payload call missing")
    _assert("log_decision(" in text, "log_decision call missing")

    idx_condition = text.find(condition)
    idx_reason = text.find(reason)
    _assert(
        idx_condition >= 0 and idx_reason > idx_condition,
        "source filter reason must be logged in source-chat guard block",
    )

    print("OK: adbot source-filter logging policy smoke passed.")


if __name__ == "__main__":
    main()
