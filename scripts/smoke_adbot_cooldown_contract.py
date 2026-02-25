#!/usr/bin/env python3
"""
Dynamic smoke for adbot cooldown/dedupe contract.

Contract:
- primary cooldown is per (chat_id, intent);
- different phrasing of same intent in same chat is still suppressed during cooldown;
- same intent in another chat is allowed;
- cooldown=0 disables guard (test-mode friendliness).
- per-pair cooldown override in listener can differ from global default.
"""

from __future__ import annotations

import asyncio
import sys
import time
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


@dataclass
class _FakeMessage:
    text: str
    id: int
    sender_id: int = 1001
    out: bool = False


class _FakeEvent:
    def __init__(self, *, text: str, chat_id: int, msg_id: int):
        self.chat_id = int(chat_id)
        self.client = self
        self.message = _FakeMessage(text=text, id=msg_id)
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        if from_peer is None:
            return [type("FwdMsg", (), {"id": 1})()]
        return [type("FwdMsg", (), {"id": 2, "fwd_from": object()})()]

    async def send_message(self, chat_id: int, text: str):
        return type("Msg", (), {"id": 3})()


class _FakePipeline:
    async def answer(self, query: str, fallback: str) -> str:
        return fallback


class _FakeInternalPipeline:
    async def get_via_internal(
        self,
        *,
        query: str,
        fallback: str,
        internal_chat_id: int,
        reply_to_message_id: int | None = None,
    ):
        from adbot.pipeline import InternalReplyResult

        return InternalReplyResult(
            text=f"ok:{query}:{internal_chat_id}",
            reason=None,
            internal_message_id=123,
            via_bot_id=456,
        )


async def _run_pair_override_check() -> None:
    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener, AdbotRuntimePair

    listener = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=CooldownGuard(3600),  # global cooldown
        pipeline=_FakePipeline(),
        internal_pipeline=_FakeInternalPipeline(),
        internal_chat_id=None,
        chat_pairs=(
            AdbotRuntimePair(
                idx=1,
                source_chat_id=-100001,
                internal_chat_id=-100101,
                sensor_uuid="esp32-a",
                fallback_building_id=1,
                fallback_section_id=2,
                reply_cooldown_sec=3600,
                label="strict",
            ),
            AdbotRuntimePair(
                idx=2,
                source_chat_id=-100002,
                internal_chat_id=-100102,
                sensor_uuid="esp32-b",
                fallback_building_id=1,
                fallback_section_id=2,
                reply_cooldown_sec=0,  # override disabled
                label="no-cooldown",
            ),
        ),
        require_real_internal_reply=False,
    )

    strict_1 = _FakeEvent(text="Дайте номер електрика", chat_id=-100001, msg_id=1)
    strict_2 = _FakeEvent(text="Потрібен телефон електрика", chat_id=-100001, msg_id=2)
    open_1 = _FakeEvent(text="Дайте номер електрика", chat_id=-100002, msg_id=3)
    open_2 = _FakeEvent(text="Потрібен телефон електрика", chat_id=-100002, msg_id=4)

    _assert(await listener.process(strict_1, source_chat_id=strict_1.chat_id) is True, "pair #1 first pass")
    _assert(
        await listener.process(strict_2, source_chat_id=strict_2.chat_id) is False,
        "pair #1 must be blocked by cooldown",
    )
    _assert(await listener.process(open_1, source_chat_id=open_1.chat_id) is True, "pair #2 first pass")
    _assert(
        await listener.process(open_2, source_chat_id=open_2.chat_id) is True,
        "pair #2 cooldown override=0 must allow repeated intent",
    )


def main() -> None:
    _bootstrap_imports()
    from adbot.cooldown import CooldownGuard

    guard = CooldownGuard(2)

    chat_id = -1001
    intent = "electrician"
    msg_a = "Дайте номер електрика"
    msg_b = "Хто має телефон електрика?"

    # First hit allowed.
    _assert(guard.allow(chat_id, intent, msg_a) is True, "first trigger must pass")
    # Same intent + different text in same chat must be blocked by intent-level cooldown.
    _assert(
        guard.allow(chat_id, intent, msg_b) is False,
        "same intent in same chat must be blocked despite different wording",
    )
    # Same intent in different chat is independent and should pass.
    _assert(
        guard.allow(chat_id - 1, intent, msg_b) is True,
        "different chat should have independent cooldown bucket",
    )
    # Different intent in same chat is independent and should pass.
    _assert(
        guard.allow(chat_id, "light_status", "Чи є світло в Ньюкасл?") is True,
        "different intent in same chat should not be blocked",
    )

    # After cooldown expires, same intent in same chat should pass again.
    time.sleep(2.05)
    _assert(
        guard.allow(chat_id, intent, msg_b) is True,
        "intent cooldown should expire and allow new trigger",
    )

    # Cooldown disabled -> always allowed.
    disabled = CooldownGuard(0)
    _assert(disabled.allow(chat_id, intent, msg_a) is True, "disabled guard first allow")
    _assert(disabled.allow(chat_id, intent, msg_a) is True, "disabled guard repeated allow")

    asyncio.run(_run_pair_override_check())

    print("OK: adbot cooldown contract smoke passed.")


if __name__ == "__main__":
    main()
