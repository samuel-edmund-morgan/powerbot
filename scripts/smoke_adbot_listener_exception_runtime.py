#!/usr/bin/env python3
"""
Dynamic smoke for adbot listener exception handling.

Checks:
- exceptions from listener path do not crash processing;
- decision log contains `listener_exception` reason.
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


class _BoomListener:
    async def process(self, event, *, source_chat_id: int) -> bool:
        raise RuntimeError("boom from listener")


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
    from adbot_main import _safe_process_new_message_event

    decision_logger = logging.getLogger("adbot.decision")
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = decision_logger.level
    old_propagate = decision_logger.propagate
    decision_logger.setLevel(logging.INFO)
    decision_logger.propagate = False
    decision_logger.addHandler(handler)
    try:
        event = _FakeEvent(
            chat_id=-100100,
            message=_FakeMessage(text="Дайте номер електрика", sender_id=12345),
        )
        handled = await _safe_process_new_message_event(
            event=event,
            listener=_BoomListener(),
            source_chat_ids={-100100},
            enabled=True,
        )
        _assert(handled is False, "listener exception path must return False")

        reasons = _extract_reasons(handler.messages)
        _assert(
            "listener_exception" in reasons,
            f"missing listener_exception decision reason, got={reasons}",
        )
    finally:
        decision_logger.removeHandler(handler)
        decision_logger.setLevel(old_level)
        decision_logger.propagate = old_propagate


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot listener-exception runtime smoke passed.")


if __name__ == "__main__":
    main()
