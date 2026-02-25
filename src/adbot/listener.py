"""Event listener for adbot source chat messages."""

from __future__ import annotations

import logging
import time
import asyncio
import os
import sqlite3
from dataclasses import dataclass

from adbot.audit import build_audit_payload, build_decision_payload, log_decision, log_match
from adbot.cooldown import CooldownGuard
from adbot.matcher import analyze_intent_match
from adbot.pipeline import InternalReplyPipeline, ResponsePipeline

logger = logging.getLogger(__name__)
LIGHT_STATUS_INTENT_CODE = "light_status"


@dataclass(frozen=True)
class CorrelationRecord:
    source_chat_id: int
    source_message_id: int
    internal_forwarded_message_id: int | None
    internal_reply_message_id: int | None
    created_at_ts: float


@dataclass(frozen=True)
class AdbotRuntimePair:
    idx: int
    source_chat_id: int
    internal_chat_id: int
    sensor_uuid: str
    fallback_building_id: int
    fallback_section_id: int
    reply_cooldown_sec: int
    label: str


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
        chat_pairs: tuple[AdbotRuntimePair, ...] | None = None,
        light_chat_bindings: dict[int, tuple[int, int]] | None = None,
        require_real_internal_reply: bool = True,
        require_source_forwarded: bool = False,
        allow_text_fallback_on_forward_failure: bool = False,
        allow_self_outgoing_e2e: bool = False,
        self_user_id: int | None = None,
        self_outgoing_prefix: str = "[E2E]",
    ):
        self._matcher_min_len = matcher_min_len
        self._matcher_max_len = matcher_max_len
        self._matcher_min_confidence = matcher_min_confidence
        self._cooldown = cooldown
        self._global_cooldown_sec = int(getattr(cooldown, "cooldown_sec", 0) or 0)
        self._pipeline = pipeline
        self._internal_pipeline = internal_pipeline
        self._internal_chat_id = internal_chat_id
        self._light_chat_bindings = dict(light_chat_bindings or {})
        self._chat_pairs = tuple(chat_pairs or ())
        self._pair_mode = len(self._chat_pairs) > 0
        self._pairs_by_source_variant: dict[int, AdbotRuntimePair] = {}
        self._pair_cooldowns: dict[int, CooldownGuard] = {}
        for pair in self._chat_pairs:
            self._pair_cooldowns[int(pair.idx)] = CooldownGuard(int(pair.reply_cooldown_sec))
            for variant in self._chat_id_variants(int(pair.source_chat_id)):
                self._pairs_by_source_variant[int(variant)] = pair
        self._db_path = str(os.getenv("DB_PATH", "/data/state.db")).strip() or "/data/state.db"
        self._require_real_internal_reply = bool(require_real_internal_reply)
        self._require_source_forwarded = bool(require_source_forwarded)
        self._allow_text_fallback_on_forward_failure = bool(allow_text_fallback_on_forward_failure)
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

    @staticmethod
    def _chat_id_variants(chat_id: int) -> tuple[int, ...]:
        value = int(chat_id or 0)
        if value == 0:
            return (0,)

        variants: list[int] = [value]
        raw = str(abs(value))

        # Bot API supergroup/chat id format -> Telethon short format.
        if value < 0 and raw.startswith("100") and len(raw) > 3:
            try:
                variants.append(-int(raw[3:]))
            except Exception:
                pass

        # Telethon short format -> Bot API supergroup/chat id format.
        if value < 0 and not raw.startswith("100"):
            try:
                variants.append(-int(f"100{raw}"))
            except Exception:
                pass

        return tuple(dict.fromkeys(variants))

    def _resolve_light_binding(self, source_chat_id: int) -> tuple[int, int] | None:
        for chat_id in self._chat_id_variants(int(source_chat_id)):
            binding = self._light_chat_bindings.get(int(chat_id))
            if binding is not None:
                return binding
        return None

    async def _deliver_source_response(
        self,
        *,
        event,
        source_chat_id: int,
        source_message_id: int,
        response_text: str,
        internal_chat_id: int,
        internal_reply_message_id: int | None,
    ) -> tuple[bool, str]:
        """Deliver response to source chat.

        Preferred mode: forward internal resident-bot message so residents
        can see response origin in chat UI.
        Fallback mode: plain text reply to original source message.
        """
        def _forward_ok(value: object) -> bool:
            if value is None:
                return False
            if isinstance(value, (list, tuple, set, dict)):
                return len(value) > 0
            return True

        has_forward_context = int(internal_chat_id or 0) != 0 and int(internal_reply_message_id or 0) > 0
        if self._require_source_forwarded and not has_forward_context:
            return False, "forward_required"
        if has_forward_context:
            reply_msg_id = int(internal_reply_message_id or 0)
            max_attempts = 4 if self._require_source_forwarded else 1
            for attempt in range(1, max_attempts + 1):
                try:
                    forwarded = await event.client.forward_messages(
                        int(source_chat_id),
                        reply_msg_id,
                        int(internal_chat_id),
                    )
                    if _forward_ok(forwarded):
                        return True, "forwarded"
                    logger.warning(
                        "forward attempt returned empty result source_chat_id=%s internal_chat_id=%s internal_msg_id=%s attempt=%s/%s",
                        source_chat_id,
                        internal_chat_id,
                        reply_msg_id,
                        attempt,
                        max_attempts,
                    )
                except TypeError:
                    try:
                        forwarded = await event.client.forward_messages(
                            entity=int(source_chat_id),
                            messages=reply_msg_id,
                            from_peer=int(internal_chat_id),
                        )
                        if _forward_ok(forwarded):
                            return True, "forwarded"
                        logger.warning(
                            "forward attempt (keyword signature) returned empty result source_chat_id=%s internal_chat_id=%s internal_msg_id=%s attempt=%s/%s",
                            source_chat_id,
                            internal_chat_id,
                            reply_msg_id,
                            attempt,
                            max_attempts,
                        )
                    except Exception:
                        logger.exception(
                            "failed to forward internal reply to source chat source_chat_id=%s internal_chat_id=%s internal_msg_id=%s attempt=%s/%s",
                            source_chat_id,
                            internal_chat_id,
                            reply_msg_id,
                            attempt,
                            max_attempts,
                        )
                except Exception:
                    logger.exception(
                        "failed to forward internal reply to source chat source_chat_id=%s internal_chat_id=%s internal_msg_id=%s attempt=%s/%s",
                        source_chat_id,
                        internal_chat_id,
                        reply_msg_id,
                        attempt,
                        max_attempts,
                    )

                if attempt < max_attempts:
                    await asyncio.sleep(0.35 * attempt)
            if self._require_source_forwarded or not self._allow_text_fallback_on_forward_failure:
                return False, "forward_required"

        try:
            await event.respond(response_text, reply_to=int(source_message_id))
            return True, "text_reply"
        except Exception:
            logger.exception("failed to reply in adbot")
            return False, "reply_error"

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

    def _resolve_pair(self, source_chat_id: int) -> AdbotRuntimePair | None:
        for variant in self._chat_id_variants(int(source_chat_id)):
            pair = self._pairs_by_source_variant.get(int(variant))
            if pair is not None:
                return pair
        return None

    def _resolve_pair_light_binding(self, pair: AdbotRuntimePair) -> tuple[int, int, str]:
        uuid = str(pair.sensor_uuid or "").strip()
        if uuid:
            try:
                conn = sqlite3.connect(self._db_path, timeout=5)
                try:
                    row = conn.execute(
                        """
                        SELECT building_id, section_id, is_active
                        FROM sensors
                        WHERE uuid=?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (uuid,),
                    ).fetchone()
                finally:
                    conn.close()

                if row is not None:
                    building_id = int(row[0] or 0)
                    section_id = int(row[1] or 0)
                    is_active = int(row[2] or 0)
                    if building_id > 0 and section_id > 0 and is_active == 1:
                        return building_id, section_id, "sensor"
            except Exception:
                logger.exception("adbot pair sensor resolve failed: pair_idx=%s uuid=%s", pair.idx, uuid)

        return int(pair.fallback_building_id), int(pair.fallback_section_id), "fallback"

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

        pair = self._resolve_pair(int(source_chat_id)) if self._pair_mode else None
        if self._pair_mode and pair is None:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="source_chat_not_allowed",
                    message_text=text,
                    meta={"pair_mode": True},
                )
            )
            return False

        pair_meta: dict[str, int | str | bool] = {"pair_mode": bool(self._pair_mode)}
        if pair is not None:
            pair_meta.update(
                {
                    "pair_idx": int(pair.idx),
                    "pair_label": str(pair.label),
                    "pair_source_chat_id": int(pair.source_chat_id),
                    "pair_internal_chat_id": int(pair.internal_chat_id),
                    "pair_sensor_uuid": str(pair.sensor_uuid),
                }
            )

        effective_internal_chat_id = int(
            pair.internal_chat_id if pair is not None else int(self._internal_chat_id or 0)
        )
        effective_cooldown_sec = int(
            pair.reply_cooldown_sec if pair is not None else int(self._global_cooldown_sec)
        )
        cooldown_guard = self._pair_cooldowns.get(int(pair.idx), self._cooldown) if pair else self._cooldown

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
                        **pair_meta,
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
        if (not is_e2e_prefixed) and (not cooldown_guard.allow(source_chat_id, intent.code, text)):
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="cooldown_skip",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        **pair_meta,
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "effective_cooldown_sec": int(effective_cooldown_sec),
                    },
                )
            )
            return False

        effective_query = str(intent.inline_query or "").strip()
        light_route_source: str | None = None
        if intent.code == LIGHT_STATUS_INTENT_CODE:
            if pair is not None:
                bound_building_id, bound_section_id, light_route_source = self._resolve_pair_light_binding(pair)
                effective_query = f"light_bind:{int(bound_building_id)}:{int(bound_section_id)}"
            else:
                binding = self._resolve_light_binding(int(source_chat_id))
                if binding:
                    bound_building_id, bound_section_id = binding
                    light_route_source = "legacy_binding"
                    effective_query = f"light_bind:{int(bound_building_id)}:{int(bound_section_id)}"

        # Keep old pipeline call for telemetry parity / fallback mode support.
        _ = await self._pipeline.answer(effective_query, intent.fallback_reply)
        payload = build_audit_payload(
            chat_id=source_chat_id,
            user_id=int(sender_id),
            intent_code=intent.code,
            message_text=text,
        )
        forwarded_message_id: int | None = None
        # Forward audit log (non-blocking on failures).
        if effective_internal_chat_id:
            audit_meta = await log_match(
                payload,
                internal_chat_id=int(effective_internal_chat_id),
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
            query=effective_query,
            fallback=intent.fallback_reply,
            internal_chat_id=int(effective_internal_chat_id),
            reply_to_message_id=forwarded_message_id,
        )
        response_text = str(internal_result.text or "").strip()
        if internal_result.reason:
            if self._require_real_internal_reply or not response_text:
                log_decision(
                    build_decision_payload(
                        chat_id=source_chat_id,
                        user_id=int(sender_id),
                        reason=str(internal_result.reason),
                        message_text=text,
                        intent_code=intent.code,
                        meta={
                            **pair_meta,
                            "match_reason": analysis.reason,
                            "best_confidence": analysis.best_confidence,
                            "internal_chat_id": int(effective_internal_chat_id or 0),
                            "forwarded_message_id": forwarded_message_id or 0,
                            "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                            "require_real": self._require_real_internal_reply,
                            "via_bot_id": int(internal_result.via_bot_id or 0),
                            "effective_cooldown_sec": int(effective_cooldown_sec),
                            "light_route_source": light_route_source or "",
                        },
                    )
                )
                return False

            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="internal_fallback_used",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        **pair_meta,
                        "source_reason": str(internal_result.reason),
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(effective_internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                        "require_real": self._require_real_internal_reply,
                        "via_bot_id": int(internal_result.via_bot_id or 0),
                        "effective_cooldown_sec": int(effective_cooldown_sec),
                        "light_route_source": light_route_source or "",
                    },
                )
            )

        if not response_text:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason="resident_no_reply",
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        **pair_meta,
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(effective_internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                        "effective_cooldown_sec": int(effective_cooldown_sec),
                        "light_route_source": light_route_source or "",
                    },
                )
            )
            return False

        delivered, delivery_mode = await self._deliver_source_response(
            event=event,
            source_chat_id=int(source_chat_id),
            source_message_id=int(getattr(message_obj, "id", 0) or 0),
            response_text=response_text,
            internal_chat_id=int(effective_internal_chat_id),
            internal_reply_message_id=int(internal_result.internal_message_id or 0) or None,
        )
        if not delivered:
            log_decision(
                build_decision_payload(
                    chat_id=source_chat_id,
                    user_id=int(sender_id),
                    reason=str(delivery_mode),
                    message_text=text,
                    intent_code=intent.code,
                    meta={
                        **pair_meta,
                        "match_reason": analysis.reason,
                        "best_confidence": analysis.best_confidence,
                        "internal_chat_id": int(effective_internal_chat_id or 0),
                        "forwarded_message_id": forwarded_message_id or 0,
                        "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                        "effective_cooldown_sec": int(effective_cooldown_sec),
                        "light_route_source": light_route_source or "",
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
                    **pair_meta,
                    "match_reason": analysis.reason,
                    "best_confidence": analysis.best_confidence,
                    "internal_chat_id": int(effective_internal_chat_id or 0),
                    "forwarded_message_id": forwarded_message_id or 0,
                    "internal_reply_message_id": int(internal_result.internal_message_id or 0),
                    "delivery_mode": str(delivery_mode),
                    "effective_cooldown_sec": int(effective_cooldown_sec),
                    "light_route_source": light_route_source or "",
                },
            )
        )
        return True
