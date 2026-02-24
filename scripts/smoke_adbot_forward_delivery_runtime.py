#!/usr/bin/env python3
"""
Runtime smoke for strict adbot delivery mode (forward-only).

Checks:
- successful source delivery uses `forward_messages` and does not post text reply;
- when source-forward fails and text fallback is disabled, listener returns no-reply;
- decision log contains explicit `forward_required` reason.
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
    async def inline_query(self, username: str, query: str):
        return _FakeInlineResponse(results=[_FakeArticle(title="⚡", description="ok")])


class _FakeInternalPipeline:
    calls: list[tuple[str, int, int | None]]

    def __init__(self):
        self.calls = []

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
        return InternalReplyResult(
            text="⚡ Електрик\n📞 067-576-22-42",
            reason=None,
            internal_message_id=900777,
            via_bot_id=123456,
        )


class _FakeInternalPipelineNoId(_FakeInternalPipeline):
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
        return InternalReplyResult(
            text="⚡ Електрик\n📞 067-576-22-42",
            reason=None,
            internal_message_id=None,
            via_bot_id=123456,
        )


class _ForwardOkClient:
    def __init__(self):
        self.forwarded_to_internal: list[tuple[int, int]] = []
        self.forwarded_to_source: list[tuple[int, int, int]] = []
        self.sent: list[tuple[int, str]] = []

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        if from_peer is None:
            message_id = int(getattr(messages, "id", 0) or 0)
            self.forwarded_to_internal.append((int(entity), message_id))
            return [type("FwdInternal", (), {"id": 700555})()]

        internal_id = int(messages[0] if isinstance(messages, (list, tuple)) else messages)
        self.forwarded_to_source.append((int(entity), internal_id, int(from_peer)))
        return [type("FwdSource", (), {"id": 800555, "fwd_from": object()})()]

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((int(chat_id), str(text)))


class _ForwardFailClient(_ForwardOkClient):
    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        if from_peer is None:
            return await super().forward_messages(entity, messages, from_peer=from_peer, **kwargs)
        raise RuntimeError("forced source-forward failure")


@dataclass
class _FakeMessage:
    text: str
    id: int
    out: bool = False
    sender_id: int = 8316649752


class _FakeEvent:
    def __init__(self, *, client, text: str, chat_id: int, msg_id: int):
        self.client = client
        self.chat_id = int(chat_id)
        self.message = _FakeMessage(text=text, id=int(msg_id))
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))


async def _run() -> None:
    _bootstrap_imports()
    import adbot.listener as listener_module
    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener
    from adbot.pipeline import PowerbotInlineClient, ResponsePipeline

    decision_log: list[dict] = []
    original_log_decision = listener_module.log_decision
    listener_module.log_decision = lambda payload: decision_log.append(dict(payload))
    try:
        provider = PowerbotInlineClient(_FakeInlineClient(), "powerbot")
        pipeline = ResponsePipeline(provider, fallback_ms=500)
        internal_pipeline = _FakeInternalPipeline()

        # Positive: forward delivery works; no text fallback.
        ok_client = _ForwardOkClient()
        listener_ok = AdbotListener(
            matcher_min_len=10,
            matcher_max_len=280,
            matcher_min_confidence=120,
            cooldown=CooldownGuard(0),
            pipeline=pipeline,
            internal_pipeline=internal_pipeline,
            internal_chat_id=777001,
            require_real_internal_reply=True,
            allow_text_fallback_on_forward_failure=False,
        )
        ok_event = _FakeEvent(
            client=ok_client,
            text="Дайте номер електрика, будь ласка",
            chat_id=-100200,
            msg_id=501,
        )
        handled_ok = await listener_ok.process(ok_event, source_chat_id=ok_event.chat_id)
        _assert(handled_ok is True, "forward-success scenario must be handled")
        _assert(not ok_event.responses, "forward-success must not emit text response")
        _assert(len(ok_client.forwarded_to_source) == 1, "forward-success must forward to source chat")

        # Negative: forward fails, fallback disabled -> no source text + explicit decision reason.
        fail_client = _ForwardFailClient()
        listener_fail = AdbotListener(
            matcher_min_len=10,
            matcher_max_len=280,
            matcher_min_confidence=120,
            cooldown=CooldownGuard(0),
            pipeline=pipeline,
            internal_pipeline=internal_pipeline,
            internal_chat_id=777001,
            require_real_internal_reply=True,
            allow_text_fallback_on_forward_failure=False,
        )
        fail_event = _FakeEvent(
            client=fail_client,
            text="Дайте номер електрика, будь ласка",
            chat_id=-100201,
            msg_id=601,
        )
        handled_fail = await listener_fail.process(fail_event, source_chat_id=fail_event.chat_id)
        _assert(handled_fail is False, "forward-fail + strict mode must not be handled as replied")
        _assert(not fail_event.responses, "strict forward-fail must not emit text fallback")
        _assert(
            any(item.get("reason") == "forward_required" for item in decision_log),
            f"expected forward_required decision log, got: {decision_log}",
        )

        # Strict forwarded mode: if internal pipeline has no message_id context,
        # listener must NOT degrade to text-reply.
        strict_noid_pipeline = _FakeInternalPipelineNoId()
        strict_noid_listener = AdbotListener(
            matcher_min_len=10,
            matcher_max_len=280,
            matcher_min_confidence=120,
            cooldown=CooldownGuard(0),
            pipeline=pipeline,
            internal_pipeline=strict_noid_pipeline,
            internal_chat_id=777001,
            require_real_internal_reply=True,
            require_source_forwarded=True,
            allow_text_fallback_on_forward_failure=True,
        )
        strict_noid_event = _FakeEvent(
            client=_ForwardOkClient(),
            text="Дайте номер електрика, будь ласка",
            chat_id=-100202,
            msg_id=701,
        )
        handled_strict_noid = await strict_noid_listener.process(
            strict_noid_event,
            source_chat_id=strict_noid_event.chat_id,
        )
        _assert(
            handled_strict_noid is False,
            "strict forwarded mode must reject delivery without internal reply message id",
        )
        _assert(
            not strict_noid_event.responses,
            "strict forwarded mode must not emit text reply when internal message id is absent",
        )
    finally:
        listener_module.log_decision = original_log_decision


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot forward-delivery runtime smoke passed.")


if __name__ == "__main__":
    main()
