#!/usr/bin/env python3
"""
Smoke check for resident single-message interactive contract.

Focuses on key interactive handlers and verifies they use render_or_edit
instead of creating fresh chat messages on each interaction.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HANDLERS_FILE = REPO_ROOT / "src" / "handlers.py"


TARGET_HANDLERS = {
    "handle_webapp_reply_keyboard",
    "reply_utilities",
    "reply_alerts",
    "reply_notifications",
    "reply_places",
    "reply_search",
    "handle_search_query",
}


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _function_calls(source: str, fn_name: str) -> tuple[int, int]:
    render_calls = source.count("render_or_edit(")
    answer_calls = source.count(".answer(")
    return render_calls, answer_calls


def main() -> None:
    _assert(HANDLERS_FILE.exists(), f"file not found: {HANDLERS_FILE}")
    text = HANDLERS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(HANDLERS_FILE))

    segments: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name in TARGET_HANDLERS:
                segment = ast.get_source_segment(text, node) or ""
                segments[node.name] = segment

    missing = sorted(TARGET_HANDLERS - set(segments))
    _assert(not missing, f"target handlers not found: {missing}")

    for fn_name, segment in sorted(segments.items()):
        render_calls, answer_calls = _function_calls(segment, fn_name)
        _assert(render_calls >= 1, f"{fn_name} must call render_or_edit at least once")
        _assert(answer_calls == 0, f"{fn_name} must avoid direct .answer(...) calls")

    # Core helper presence + fallback reason log contract.
    _assert("def _ui_last_message_key(" in text, "missing _ui_last_message_key helper")
    _assert("async def render_or_edit(" in text, "missing render_or_edit helper")
    for reason in ("edit_failed", "message_deleted", "not_editable", "race_recovered"):
        _assert(reason in text, f"missing render_or_edit fallback reason `{reason}`")

    print("OK: single-message interactive runtime smoke passed.")


if __name__ == "__main__":
    main()

