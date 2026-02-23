#!/usr/bin/env python3
"""
Integration smoke for adbot listener/pipeline with mock Telethon-like stubs.

Checks:
- intent match -> inline query fetch -> reply as message reply
- audit forward is emitted
- cooldown dedupe suppresses identical repeated triggers
- fallback is used when inline provider returns no result
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


@dataclass
class _FakeArticle:
    title: str
    description: str


@dataclass
class _FakeInlineResponse:
    results: list[_FakeArticle]


class _FakeInlineClient:
    def __init__(self, responses: dict[str, list[_FakeArticle]]):
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    async def inline_query(self, username: str, query: str):
        self.calls.append((username, query))
        return _FakeInlineResponse(results=self._responses.get(query, []))


class _FakeForwarder:
    def __init__(self):
        self.forwarded: list[tuple[int, int]] = []
        self.sent: list[tuple[int, str]] = []

    async def forward_messages(self, chat_id: int, message):
        message_id = int(getattr(message, "id", 0) or 0)
        self.forwarded.append((int(chat_id), message_id))

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((int(chat_id), str(text)))


@dataclass
class _FakeMessage:
    text: str
    id: int
    out: bool = False
    sender_id: int = 1001


class _FakeEvent:
    def __init__(
        self,
        *,
        text: str,
        chat_id: int,
        msg_id: int,
        forwarder: _FakeForwarder,
        sender_id: int = 1001,
        out: bool = False,
    ):
        self.chat_id = int(chat_id)
        self.client = forwarder
        self.message = _FakeMessage(
            text=text,
            id=int(msg_id),
            sender_id=int(sender_id),
            out=bool(out),
        )
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))


async def _run() -> None:
    _bootstrap_imports()

    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener
    from adbot.pipeline import PowerbotInlineClient, ResponsePipeline

    # Positive flow: electrician query resolved via inline result.
    fake_inline = _FakeInlineClient(
        {
            "електрик": [_FakeArticle(title="⚡ Електрик", description="📞 067-576-22-42")],
            "сантехнік": [],
            "диспетчер ліфтів": [_FakeArticle(title="🛗 Ліфти", description="📞 2 контакти")],
        }
    )
    provider = PowerbotInlineClient(fake_inline, "powerbot")
    pipeline = ResponsePipeline(provider, fallback_ms=500)
    cooldown = CooldownGuard(3600)
    forwarder = _FakeForwarder()
    listener = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=cooldown,
        pipeline=pipeline,
        internal_chat_id=777001,
    )

    evt_ok = _FakeEvent(
        text="Дайте номер електрика, будь ласка",
        chat_id=-100123,
        msg_id=501,
        forwarder=forwarder,
    )
    handled = await listener.process(evt_ok, source_chat_id=evt_ok.chat_id)
    _assert(handled is True, "expected listener to handle electrician message")
    _assert(len(fake_inline.calls) == 1, f"inline query not called exactly once: {fake_inline.calls}")
    _assert(fake_inline.calls[0][1] == "електрик", f"unexpected inline query: {fake_inline.calls}")
    _assert(len(evt_ok.responses) == 1, "expected one response")
    _assert(evt_ok.responses[0][1] == 501, f"response should be reply to original message: {evt_ok.responses}")
    _assert("⚡ Електрик" in evt_ok.responses[0][0], f"unexpected response body: {evt_ok.responses}")
    _assert(len(forwarder.forwarded) == 1, "expected one forwarded source message to internal chat")
    _assert(forwarder.forwarded[0] == (777001, 501), f"unexpected forwarded payload: {forwarder.forwarded}")
    _assert(len(forwarder.sent) == 1, "expected one audit summary message")
    _assert("intent" in forwarder.sent[0][1], f"audit payload missing intent: {forwarder.sent}")

    # Duplicate guard by Telegram message id (same chat + same message_id) should suppress re-processing
    # even when text/intent match and regardless of cooldown settings.
    evt_dup_same_id = _FakeEvent(
        text="Дайте номер електрика, будь ласка",
        chat_id=-100123,
        msg_id=501,  # same as evt_ok above
        forwarder=forwarder,
    )
    handled_dup_same_id = await listener.process(evt_dup_same_id, source_chat_id=evt_dup_same_id.chat_id)
    _assert(
        handled_dup_same_id is False,
        "expected duplicate-message-id guard to suppress second processing of same message id",
    )
    _assert(
        len(evt_dup_same_id.responses) == 0,
        "duplicate-message-id event must not produce response",
    )

    # Cooldown dedupe for same chat+intent+message.
    evt_dup = _FakeEvent(
        text="Дайте номер електрика, будь ласка",
        chat_id=-100123,
        msg_id=502,
        forwarder=forwarder,
    )
    handled_dup = await listener.process(evt_dup, source_chat_id=evt_dup.chat_id)
    _assert(handled_dup is False, "expected cooldown to suppress duplicate message")
    _assert(len(evt_dup.responses) == 0, "duplicate should not produce response")

    # Cooldown is per (chat,intent), not only by exact text hash.
    evt_same_intent_other_text = _FakeEvent(
        text="Підкажіть, будь ласка, телефон електрика",
        chat_id=-100123,
        msg_id=503,
        forwarder=forwarder,
    )
    handled_same_intent = await listener.process(
        evt_same_intent_other_text,
        source_chat_id=evt_same_intent_other_text.chat_id,
    )
    _assert(
        handled_same_intent is False,
        "expected cooldown to suppress same intent with different wording in same chat",
    )
    _assert(len(evt_same_intent_other_text.responses) == 0, "suppressed same-intent message must not reply")

    # E2E-prefixed probes must bypass cooldown to keep deploy_test deterministic
    # when adbot E2E suite runs repeatedly.
    e2e_forwarder = _FakeForwarder()
    e2e_pipeline = ResponsePipeline(provider, fallback_ms=500)
    e2e_listener = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=CooldownGuard(3600),
        pipeline=e2e_pipeline,
        internal_chat_id=None,
        allow_self_outgoing_e2e=True,
        self_user_id=5555,
        self_outgoing_prefix="[E2E]",
    )
    evt_e2e_1 = _FakeEvent(
        text="[E2E] Дайте номер електрика, будь ласка",
        chat_id=-10012345,
        msg_id=511,
        forwarder=e2e_forwarder,
        sender_id=7777,
        out=False,
    )
    evt_e2e_2 = _FakeEvent(
        text="[E2E] Дайте номер електрика, будь ласка",
        chat_id=-10012345,
        msg_id=512,
        forwarder=e2e_forwarder,
        sender_id=7777,
        out=False,
    )
    handled_e2e_1 = await e2e_listener.process(evt_e2e_1, source_chat_id=evt_e2e_1.chat_id)
    handled_e2e_2 = await e2e_listener.process(evt_e2e_2, source_chat_id=evt_e2e_2.chat_id)
    _assert(handled_e2e_1 is True and handled_e2e_2 is True, "expected E2E-prefixed probes to bypass cooldown")
    _assert(len(evt_e2e_1.responses) == 1 and len(evt_e2e_2.responses) == 1, "expected both E2E probes to reply")

    # Fallback flow: matched intent with empty inline result must return fallback text.
    evt_fallback = _FakeEvent(
        text="Дайте номер сантехніка будь ласка",
        chat_id=-100124,
        msg_id=601,
        forwarder=forwarder,
    )
    handled_fb = await listener.process(evt_fallback, source_chat_id=evt_fallback.chat_id)
    _assert(handled_fb is True, "fallback flow should still be handled")
    _assert(len(evt_fallback.responses) == 1, "fallback should send one response")
    _assert("сантехніка" in evt_fallback.responses[0][0].lower(), f"unexpected fallback response: {evt_fallback.responses}")

    # Non-matching text should be ignored.
    evt_skip = _FakeEvent(
        text="Привіт, як справи?",
        chat_id=-100125,
        msg_id=701,
        forwarder=forwarder,
    )
    handled_skip = await listener.process(evt_skip, source_chat_id=evt_skip.chat_id)
    _assert(handled_skip is False, "non-intent text should not be handled")
    _assert(len(evt_skip.responses) == 0, "non-intent text should not produce response")

    # Additional service intent flow (elevator).
    evt_elev = _FakeEvent(
        text="Дайте номер диспетчера ліфтів",
        chat_id=-100126,
        msg_id=801,
        forwarder=forwarder,
    )
    handled_elev = await listener.process(evt_elev, source_chat_id=evt_elev.chat_id)
    _assert(handled_elev is True, "elevator intent should be handled")
    _assert(any(call[1] == "диспетчер ліфтів" for call in fake_inline.calls), f"missing elevator inline call: {fake_inline.calls}")
    _assert(len(evt_elev.responses) == 1, "elevator flow should send one response")

    # Critical flow from AGENTS backlog: pass/parking phrasing.
    evt_pass = _FakeEvent(
        text="Де оформити перепустку в паркінг?",
        chat_id=-100127,
        msg_id=901,
        forwarder=forwarder,
    )
    handled_pass = await listener.process(evt_pass, source_chat_id=evt_pass.chat_id)
    _assert(handled_pass is True, "car-pass intent should be handled for parking phrasing")
    _assert(
        any(str(call[1]).startswith("перепустка") for call in fake_inline.calls),
        f"missing car-pass inline call: {fake_inline.calls}",
    )
    _assert(len(evt_pass.responses) == 1, "car-pass flow should send one response")


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot pipeline integration smoke passed.")


if __name__ == "__main__":
    main()
