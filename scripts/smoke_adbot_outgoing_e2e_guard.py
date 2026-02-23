#!/usr/bin/env python3
"""
Dynamic smoke for adbot self-outgoing E2E guard.

Ensures:
- outgoing self-messages are skipped by default;
- when explicit test-only guard is enabled, prefixed outgoing messages are handled;
- non-prefixed outgoing messages remain blocked even with test-only mode enabled.
"""

from __future__ import annotations

import asyncio
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


class _FakeInlineProvider:
    async def fetch_block(self, query: str):
        return f"inline::{query}"


@dataclass
class _FakeMessage:
    text: str
    id: int
    sender_id: int = 1001
    out: bool = True


class _FakeEvent:
    def __init__(self, *, text: str, msg_id: int, sender_id: int = 1001):
        self.chat_id = -100555
        self.message = _FakeMessage(text=text, id=msg_id, sender_id=sender_id, out=True)
        self.client = None
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))


async def _run() -> None:
    _bootstrap_imports()
    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener
    from adbot.pipeline import ResponsePipeline

    # 1) Default: outgoing self-message is skipped.
    listener_default = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=CooldownGuard(0),
        pipeline=ResponsePipeline(_FakeInlineProvider(), fallback_ms=300),
        internal_chat_id=None,
    )
    evt_default = _FakeEvent(text="[E2E] Дайте номер електрика", msg_id=1)
    handled_default = await listener_default.process(evt_default, source_chat_id=evt_default.chat_id)
    _assert(handled_default is False, "outgoing self-message must be skipped by default")
    _assert(not evt_default.responses, "default skip must not send response")

    # 2) Test-only enabled + matching prefix: allowed.
    listener_enabled = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=CooldownGuard(0),
        pipeline=ResponsePipeline(_FakeInlineProvider(), fallback_ms=300),
        internal_chat_id=None,
        allow_self_outgoing_e2e=True,
        self_user_id=1001,
        self_outgoing_prefix="[E2E]",
    )
    evt_allowed = _FakeEvent(text="[E2E] Дайте номер електрика", msg_id=2)
    handled_allowed = await listener_enabled.process(evt_allowed, source_chat_id=evt_allowed.chat_id)
    _assert(handled_allowed is True, "prefixed outgoing self-message must be allowed in e2e mode")
    _assert(len(evt_allowed.responses) == 1, "allowed e2e message must produce response")

    # 3) Even in e2e mode, outgoing without prefix stays blocked.
    evt_blocked = _FakeEvent(text="Дайте номер електрика", msg_id=3)
    handled_blocked = await listener_enabled.process(evt_blocked, source_chat_id=evt_blocked.chat_id)
    _assert(handled_blocked is False, "outgoing self-message without prefix must remain blocked")
    _assert(not evt_blocked.responses, "blocked message must not produce response")


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot outgoing E2E guard smoke passed.")


if __name__ == "__main__":
    main()
