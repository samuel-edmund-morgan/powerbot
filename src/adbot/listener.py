"""Event listener for adbot source chat messages."""

from __future__ import annotations

import logging

from adbot.audit import build_audit_payload, build_decision_payload, log_decision, log_match
from adbot.cooldown import CooldownGuard
from adbot.matcher import analyze_intent_match
from adbot.pipeline import ResponsePipeline

logger = logging.getLogger(__name__)


class AdbotListener:
    def __init__(
        self,
        *,
        matcher_min_len: int,
        matcher_max_len: int,
        matcher_min_confidence: int,
        cooldown: CooldownGuard,
        pipeline: ResponsePipeline,
        internal_chat_id: int | None = None,
        allow_self_outgoing_e2e: bool = False,
        self_user_id: int | None = None,
        self_outgoing_prefix: str = "[E2E]",
    ):
        self._matcher_min_len = matcher_min_len
        self._matcher_max_len = matcher_max_len
        self._matcher_min_confidence = matcher_min_confidence
        self._cooldown = cooldown
        self._pipeline = pipeline
        self._internal_chat_id = internal_chat_id
        self._allow_self_outgoing_e2e = bool(allow_self_outgoing_e2e)
        self._self_user_id = int(self_user_id) if self_user_id else None
        self._self_outgoing_prefix = str(self_outgoing_prefix or "[E2E]").strip() or "[E2E]"

    async def process(self, event, *, source_chat_id: int) -> bool:
        """
        Process one incoming group message. Returns True if a response sent.
        """
        message_obj = event.message if hasattr(event, "message") else event
        text = (getattr(message_obj, "text", "") or "").strip()
        sender_id = int(getattr(message_obj, "sender_id", 0) or 0)
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

        response_text = await self._pipeline.answer(intent.inline_query, intent.fallback_reply)
        payload = build_audit_payload(
            chat_id=source_chat_id,
            user_id=int(sender_id),
            intent_code=intent.code,
            message_text=text,
        )
        # Forward audit log (non-blocking on failures).
        if self._internal_chat_id:
            await log_match(
                payload,
                internal_chat_id=self._internal_chat_id,
                forwarder=event.client,
                original_message=message_obj,
            )

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
                    },
                )
            )
            return False
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
                },
            )
        )
        return True
