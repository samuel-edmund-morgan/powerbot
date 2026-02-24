#!/usr/bin/env python3
"""
Integration smoke for adbot listener/pipeline with mock Telethon-like stubs.

Checks:
- intent match -> inline query fetch -> source delivery prefers forwarded internal reply
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
        self.forwarded_to_internal: list[tuple[int, int]] = []
        self.forwarded_to_source: list[tuple[int, int, int]] = []
        self.sent: list[tuple[int, str]] = []

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        # Audit-path forward: source -> internal chat.
        if from_peer is None:
            message_id = int(getattr(messages, "id", 0) or 0)
            self.forwarded_to_internal.append((int(entity), message_id))
            return [type("FwdMsg", (), {"id": 700001})()]

        # Source-delivery path: internal reply -> source chat.
        if isinstance(messages, (list, tuple)):
            internal_message_id = int(messages[0] if messages else 0)
        else:
            internal_message_id = int(messages or 0)
        self.forwarded_to_source.append((int(entity), internal_message_id, int(from_peer)))
        return [type("FwdMsg", (), {"id": 800001, "fwd_from": object()})()]

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((int(chat_id), str(text)))


class _FakeInternalPipeline:
    def __init__(self, results: dict[str, tuple[str | None, str | None]]):
        # query -> (text, reason)
        self._results = results
        self.calls: list[tuple[str, int, int | None]] = []

    async def get_via_internal(
        self,
        *,
        query: str,
        fallback: str,
        internal_chat_id: int,
        reply_to_message_id: int | None = None,
    ):
        from adbot.pipeline import InternalReplyResult

        self.calls.append((query, int(internal_chat_id), reply_to_message_id))
        text, reason = self._results.get(query, (fallback, None))
        return InternalReplyResult(
            text=text,
            reason=reason,
            internal_message_id=900001,
            via_bot_id=123456,
        )


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
    internal_pipeline = _FakeInternalPipeline(
        {
            "електрик": ("⚡ Електрик\n📞 067-576-22-42", None),
            "сантехнік": ("🔧 Сантехнік\n📞 067-000-00-00", None),
            "диспетчер ліфтів": ("🛗 Ліфти\n📞 2 контакти", None),
            "перепустка авто": ("🚗 Перепустка авто\nОформлення через сервісну службу", None),
            "light_bind:1:2": ("☀️ Стан електропостачання в Ньюкасл секція 2", None),
        }
    )
    cooldown = CooldownGuard(3600)
    forwarder = _FakeForwarder()
    listener = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=cooldown,
        pipeline=pipeline,
        internal_pipeline=internal_pipeline,
        internal_chat_id=777001,
        light_chat_bindings={-100128: (1, 2)},
        require_real_internal_reply=True,
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
    _assert(len(evt_ok.responses) == 0, "forward-delivery should not require text fallback response")
    _assert(len(forwarder.forwarded_to_internal) == 1, "expected one forwarded source message to internal chat")
    _assert(
        forwarder.forwarded_to_internal[0] == (777001, 501),
        f"unexpected internal-forward payload: {forwarder.forwarded_to_internal}",
    )
    _assert(len(forwarder.forwarded_to_source) == 1, "expected one forwarded internal reply to source chat")
    _assert(
        forwarder.forwarded_to_source[0] == (-100123, 900001, 777001),
        f"unexpected source-forward payload: {forwarder.forwarded_to_source}",
    )
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
    e2e_internal_pipeline = _FakeInternalPipeline(
        {"електрик": ("⚡ Електрик\n📞 067-576-22-42", None)}
    )
    e2e_listener = AdbotListener(
        matcher_min_len=10,
        matcher_max_len=280,
        matcher_min_confidence=120,
        cooldown=CooldownGuard(3600),
        pipeline=e2e_pipeline,
        internal_pipeline=e2e_internal_pipeline,
        internal_chat_id=None,
        require_real_internal_reply=False,
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

    # Fallback-like flow via internal pipeline: when internal text exists, it is used.
    evt_fallback = _FakeEvent(
        text="Дайте номер сантехніка будь ласка",
        chat_id=-100124,
        msg_id=601,
        forwarder=forwarder,
    )
    handled_fb = await listener.process(evt_fallback, source_chat_id=evt_fallback.chat_id)
    _assert(handled_fb is True, "fallback flow should still be handled")
    _assert(len(evt_fallback.responses) == 0, "fallback flow should prefer forwarded internal reply")
    _assert(
        any(item[0] == -100124 and item[2] == 777001 for item in forwarder.forwarded_to_source),
        f"fallback flow should forward internal reply to source chat: {forwarder.forwarded_to_source}",
    )

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
    _assert(len(evt_elev.responses) == 0, "elevator flow should prefer forwarded internal reply")
    _assert(
        any(item[0] == -100126 and item[2] == 777001 for item in forwarder.forwarded_to_source),
        f"elevator flow should forward internal reply: {forwarder.forwarded_to_source}",
    )

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
    _assert(len(evt_pass.responses) == 0, "car-pass flow should prefer forwarded internal reply")
    _assert(
        any(item[0] == -100127 and item[2] == 777001 for item in forwarder.forwarded_to_source),
        f"car-pass flow should forward internal reply: {forwarder.forwarded_to_source}",
    )

    # Chat binding flow for light status: source chat is mapped to building/section.
    evt_light_bound = _FakeEvent(
        text="Чи є світло?",
        chat_id=-100128,
        msg_id=951,
        forwarder=forwarder,
    )
    handled_light_bound = await listener.process(evt_light_bound, source_chat_id=evt_light_bound.chat_id)
    _assert(handled_light_bound is True, "light-bound intent should be handled")
    _assert(len(evt_light_bound.responses) == 0, "light-bound flow should prefer forwarded internal reply")
    _assert(
        ("light_bind:1:2", 777001, 700001) in internal_pipeline.calls,
        f"light-bound query must be rewritten for internal pipeline: {internal_pipeline.calls}",
    )

    # Unbound light-status chat: must keep generic query (no light_bind rewrite).
    evt_light_unbound = _FakeEvent(
        text="Чи є світло?",
        chat_id=-100129,
        msg_id=952,
        forwarder=forwarder,
    )
    handled_light_unbound = await listener.process(
        evt_light_unbound,
        source_chat_id=evt_light_unbound.chat_id,
    )
    _assert(handled_light_unbound is True, "light-status intent in unbound chat should be handled")
    _assert(
        ("світло", 777001, 700001) in internal_pipeline.calls,
        f"unbound light chat must use generic query: {internal_pipeline.calls}",
    )


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot pipeline integration smoke passed.")


if __name__ == "__main__":
    main()
