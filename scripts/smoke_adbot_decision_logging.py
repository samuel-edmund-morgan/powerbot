#!/usr/bin/env python3
"""
Dynamic smoke for adbot decision logging contract.

Ensures listener emits structured decision logs for:
- no intent match;
- cooldown skip;
- successful reply.
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


class _FakeInlineProvider:
    async def fetch_block(self, query: str):
        return f"inline::{query}"


@dataclass
class _FakeMessage:
    text: str
    id: int
    out: bool = False
    sender_id: int = 1001


class _FakeEvent:
    def __init__(self, *, text: str, msg_id: int, sender_id: int = 1001):
        self.chat_id = -100900
        self.message = _FakeMessage(text=text, id=msg_id, sender_id=sender_id)
        self.client = None
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))


def _collect_decision_reasons(log_lines: list[str]) -> list[str]:
    reasons: list[str] = []
    marker = "adbot decision: "
    for line in log_lines:
        idx = line.find(marker)
        if idx < 0:
            continue
        payload_raw = line[idx + len(marker) :].strip()
        try:
            payload = json.loads(payload_raw)
        except Exception:
            continue
        reason = str(payload.get("reason") or "").strip()
        if reason:
            reasons.append(reason)
    return reasons


async def _run() -> None:
    _bootstrap_imports()
    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener
    from adbot.pipeline import ResponsePipeline

    decision_logger = logging.getLogger("adbot.audit")
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = decision_logger.level
    old_propagate = decision_logger.propagate
    decision_logger.setLevel(logging.INFO)
    decision_logger.propagate = False
    decision_logger.addHandler(handler)

    try:
        listener = AdbotListener(
            matcher_min_len=10,
            matcher_max_len=280,
            matcher_min_confidence=120,
            cooldown=CooldownGuard(3600),
            pipeline=ResponsePipeline(_FakeInlineProvider(), fallback_ms=300),
            internal_chat_id=None,
        )

        # 1) no intent
        evt_no_intent = _FakeEvent(text="Привіт, гарного дня", msg_id=101)
        handled_no_intent = await listener.process(evt_no_intent, source_chat_id=evt_no_intent.chat_id)
        _assert(handled_no_intent is False, "no-intent case must be ignored")

        # 2) first handled + 3) cooldown on repeated same intent wording
        evt_ok = _FakeEvent(text="Дайте номер електрика, будь ласка", msg_id=201)
        handled_ok = await listener.process(evt_ok, source_chat_id=evt_ok.chat_id)
        _assert(handled_ok is True, "first intent message must be handled")
        _assert(len(evt_ok.responses) == 1, "handled event must produce reply")

        evt_cooldown = _FakeEvent(text="Підкажіть, будь ласка, телефон електрика", msg_id=202)
        handled_cooldown = await listener.process(evt_cooldown, source_chat_id=evt_cooldown.chat_id)
        _assert(handled_cooldown is False, "second same-intent message must be cooldown-skipped")

        reasons = _collect_decision_reasons(handler.messages)
        _assert("no_intent_match" in reasons, f"missing no_intent_match reason, got={reasons}")
        _assert("replied" in reasons, f"missing replied reason, got={reasons}")
        _assert("cooldown_skip" in reasons, f"missing cooldown_skip reason, got={reasons}")
    finally:
        decision_logger.removeHandler(handler)
        decision_logger.setLevel(old_level)
        decision_logger.propagate = old_propagate


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot decision logging smoke passed.")


if __name__ == "__main__":
    main()
