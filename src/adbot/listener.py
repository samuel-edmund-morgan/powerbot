"""Event listener for adbot source chat messages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from adbot.audit import build_audit_payload, build_decision_payload, log_decision, log_match
from adbot.cooldown import CooldownGuard
from adbot.matcher import analyze_intent_match
from adbot.pipeline import InternalReplyPipeline, ResponsePipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorrelationRecord:
    source_chat_id: int
    source_message_id: int
    internal_forwarded_message_id: int | None
    internal_reply_message_id: int | None
    created_at_ts: float


class AdbotListener:
    def __init__(
        self,
        *,
        matcher_min_len: int,
        matcher_max_len: int,
        matcher_min_confidence: int,
        cooldown: CooldownGuard,
        pipeline: ResponsePipeline,
        internal_pipeline: InternalReplyPipeline,
        internal_chat_id: int | None = None,
        require_real_internal_reply: bool = True,
        allow_self_outgoing_e2e: bool = False,
        self_user_id: int | None = None,
        self_outgoing_prefix: str = "[E2E]",
    ):
        self._matcher_min_len = matcher_min_len
        self._matcher_max_len = matcher_max_len
        self._matcher_min_confidence = matcher_min_confidence
        self._cooldown = cooldown
        self._pipeline = pipeline
        self._internal_pipeline = internal_pipeline
        self._internal_chat_id = internal_chat_id
        self._require_real_internal_reply = bool(require_real_internal_reply)
        self._allow_self_outgoing_e2e = bool(allow_self_outgoing_e2e)
        self._self_user_id = int(self_user_id) if self_user_id else None
        self._self_outgoing_prefix = str(self_outgoing_prefix or "[E2E]").strip() or "[E2E]"
        # Guard against duplicate handling of the same Telegram message
        # (e.g. NewMessage + self-outgoing poll fallback race).
        self._seen_message_events: dict[str, float] = {}
        self._seen_message_ttl_sec = 900
        # Correlation map for source -> internal flow.
        self._correlations: dict[str, CorrelationRecord] = {}
        self._correlation_ttl_sec = 6 * 3600

    def _dedupe_event_key(self, chat_id: int, message_id: int) -> str:
        return f"{int(chat_id)}:{int(message_id)}"

    def _is_duplicate_message_event(self, chat_id: int, message_id: int) -> bool:
        if int(message_id or 0) <= 0:
            return False
        now = time.time()
        key = self._dedupe_event_key(chat_id, message_id)
        ts = self._seen_message_events.get(key)
        if ts is not None and now - ts < self._seen_message_ttl_sec:
            return True

        self._seen_message_events[key] = now
        # Cheap periodic cleanup to prevent unbounded growth.
        if len(self._seen_message_events) > 10_000:
            cutoff = now - self._seen_message_ttl_sec
            self._seen_message_events = {
                k: v for k, v in self._seen_message_events.items() if v >= cutoff
            }
        return False

    def _add_correlation(
        self,
        *,
        source_chat_id: int,
        source_message_id: int,
        internal_forwarded_message_id: int | None,
        internal_reply_message_id: int | None,
    ) -> None:
        key = self._dedupe_event_key(source_chat_id, source_message_id)
        now = time.time()
        self._correlations[key] = CorrelationRecord(
            source_chat_id=int(source_chat_id),
            source_message_id=int(source_message_id),
            internal_forwarded_message_id=internal_forwarded_message_id,
            internal_reply_message_id=internal_reply_message_id,
            created_at_ts=now,
        )
        if len(self._correlations) > 10_000:
            cutoff = now - self._correlation_ttl_sec
            self._correlations = {
                k: v for k, v in self._correlations.items() if v.created_at_ts >= cutoff
            }

    async def process(self, event, *, source_chat_id: int) -> bool:
        """
        Process one incoming group message. Returns True if a response sent.
        """
        message_obj = event.message if hasattr(event, "message") else event
        text = (getattr(message_obj, "text", "") or "").strip()
        message_id = int(getattr(message_obj, "id", 0) or 0)
        sender_id = int(getattr(message_obj, "sender_id", 0) or 0)
        if self._is_duplicate_message_event(source_chat_id, message_id):
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=sender_id or None,
                    reason="duplicate_message_event",
                    message_text=text,
                )
            )
            return False

        is_e2e_prefixed = self._allow_self_outgoing_e2e and text.startswith(self._self_outgoing_prefix)
        if not text:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=sender_id or None,
                    reason="empty_text",
                )
            )
            return False

        # Skip bot/system messages and short noise.
        if bool(getattr(message_obj, "out", False)):
            allow_outgoing = (
                self._allow_self_outgoing_e2e
                and bool(self._self_user_id)
                and sender_id == self._self_user_id
                and text.startswith(self._self_outgoing_prefix)
            )
            if allow_outgoing:
                log_decision(
                    build_decision_payload(
                        chat_id=source_chat_id,
                        user_id=sender_id or None,
                        reason="outgoing_e2e_allowed",
                        message_text=text,
                    )
                )
            else:
                log_decision(
                    build_decision_payload(
                        chat_id=source_chat_id,
                        user_id=sender_id or None,
                        reason="outgoing_or_system",
                        message_text=text,
                    )
                )
                return False
        if not sender_id:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=None,
                    reason="missing_sender_id",
                    message_text=text,
                )
            )
            return False

        analysis = analyze_intent_match(
            text,
            min_len=self._matcher_min_len,
            max_len=self._matcher_max_len,
            min_confidence=self._matcher_min_confidence,
        )
        intent = analysis.intent
        if intent is None:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="no_intent_match",
                    message_text=text,
                    meta={
                        "match_reason": analysis.reason,
                        "text_len": analysis.text_len,
                        "token_count": analysis.token_count,
                        "best_intent": analysis.best_intent,
                        "best_confidence": analysis.best_confidence,
                        "best_signals": analysis.best_signals,
                        "min_confidence": self._matcher_min_confidence,
                    },
                )
            )
            return False

        # In test E2E mode we intentionally allow repeated prefixed probes
        # without waiting full chat-level cooldown window.
        if (not is_e2e_prefixed) and (not self._cooldown.allow(source_chat_id, intent.code, text)):
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="cooldown_skip",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                    },
                )
            )
            return False

        # Keep old pipeline call for telemetry parity / fallback mode support.
        _ = await self._pipeline.answer(intent.inline_query, intent.fallback_reply)
        payload = build_audit_payload(
            chat_id=source_chat_id,
            user_id=int(sender_id),
            intent_code=intent.code,
            message_text=text,
        )
        forwarded_message_id: int | None = None
        # Forward audit log (non-blocking on failures).
        if self._internal_chat_id:
            audit_meta = await log_match(
                payload,
                internal_chat_id=self._internal_chat_id,
                forwarder=event.client,
                original_message=message_obj,
            )
            if isinstance(audit_meta, dict):
                raw = audit_meta.get("forwarded_message_id")
                try:
                    forwarded_message_id = int(raw) if raw is not None else None
                except Exception:
                    forwarded_message_id = None

        internal_result = await self._internal_pipeline.get_via_internal(
            query=intent.inline_query,
            fallback=intent.fallback_reply,
            internal_chat_id=int(self._internal_chat_id or 0),
            reply_to_message_id=forwarded_message_id,
        )
        if internal_result.reason:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason=str(internal_result.reason),
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(self._internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                        "require_real": self._require_real_internal_reply,
                        "via_bot_id": int(internal_result.via_bot_id or 0),
                    },
                )
            )
            return False

        response_text = str(internal_result.text or "").strip()
        if not response_text:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="resident_no_reply",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(self._internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                    },
                )
            )
            return False

        try:
            await event.respond(response_text, reply_to=message_obj.id)
        except Exception:
            logger.exception("failed to reply in adbot")
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="reply_error",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(self._internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                    },
                )
            )
            return False

        self._add_correlation(
            source_chat_id=source_chat_id,
            source_message_id=int(message_id or 0),
            internal_forwarded_message_id=forwarded_message_id,
            internal_reply_message_id=int(internal_result.internal_message_id or 0),
        )
        log_decision(
            build_decision_payload(
                chat_id=source_chat_id,
                user_id=int(sender_id),
                reason="replied",
                message_text=text,
                intent_code=intent.code,
                meta={
                    "match_reason": analysis.reason,
                    "best_confidence": analysis.best_confidence,
                    "internal_chat_id": int(self._internal_chat_id or 0),
                    "forwarded_message_id": forwarded_message_id or 0,
                    "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                },
            )
        )
        return True
