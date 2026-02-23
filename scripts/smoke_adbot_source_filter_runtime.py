#!/usr/bin/env python3
"""
Dynamic smoke for adbot source-filter runtime behavior.

Checks:
- non-allowlisted chat is skipped and logs `source_chat_not_allowed`;
- allowlisted chat is delegated to listener when enabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


@dataclass
class _FakeMessage:
    text: str
    sender_id: int


@dataclass
class _FakeEvent:
    chat_id: int
    message: _FakeMessage


class _FakeListener:
    def __init__(self):
        self.calls: list[int] = []

    async def process(self, event, *, source_chat_id: int) -> bool:
        self.calls.append(int(source_chat_id))
        return True


def _extract_reasons(lines: list[str]) -> list[str]:
    reasons: list[str] = []
    marker = "adbot decision: "
    for line in lines:
        idx = line.find(marker)
        if idx < 0:
            continue
        raw = line[idx + len(marker) :].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        reason = str(payload.get("reason") or "").strip()
        if reason:
            reasons.append(reason)
    return reasons


async def _run() -> None:
    _bootstrap_imports()
    from adbot_main import _process_new_message_event

    decision_logger = logging.getLogger("adbot.decision")
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = decision_logger.level
    old_propagate = decision_logger.propagate
    decision_logger.setLevel(logging.INFO)
    decision_logger.propagate = False
    decision_logger.addHandler(handler)
    try:
        listener = _FakeListener()
        allowlist = {-100100}

        blocked = _FakeEvent(
            chat_id=-100200,
            message=_FakeMessage(text="Дайте номер електрика", sender_id=12345),
        )
        delegated_blocked = await _process_new_message_event(
            event=blocked,
            listener=listener,
            source_chat_ids=allowlist,
            enabled=True,
        )
        _assert(delegated_blocked is False, "blocked chat must not delegate to listener")
        _assert(listener.calls == [], f"listener must not be called for blocked chat: {listener.calls}")

        allowed = _FakeEvent(
            chat_id=-100100,
            message=_FakeMessage(text="Дайте номер електрика", sender_id=12345),
        )
        delegated_allowed = await _process_new_message_event(
            event=allowed,
            listener=listener,
            source_chat_ids=allowlist,
            enabled=True,
        )
        _assert(delegated_allowed is True, "allowlisted chat must delegate to listener")
        _assert(listener.calls == [-100100], f"unexpected listener calls: {listener.calls}")

        reasons = _extract_reasons(handler.messages)
        _assert(
            "source_chat_not_allowed" in reasons,
            f"missing source_chat_not_allowed decision reason, got={reasons}",
        )
    finally:
        decision_logger.removeHandler(handler)
        decision_logger.setLevel(old_level)
        decision_logger.propagate = old_propagate


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot source-filter runtime smoke passed.")


if __name__ == "__main__":
    main()
