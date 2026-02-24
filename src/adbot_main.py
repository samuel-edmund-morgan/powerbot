#!/usr/bin/env python3
"""Standalone adbot runtime (Telethon)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from adbot.cooldown import CooldownGuard
from adbot.listener import AdbotListener
from adbot.pipeline import InternalReplyPipeline, PowerbotInlineClient, ResponsePipeline
from adbot.audit import build_decision_payload, configure_decision_logging, log_decision
from adbot_main_config import AdbotConfig, build_config

logger = logging.getLogger(__name__)


@dataclass
class _PolledMessageEvent:
    """Minimal event adapter for polled Telethon messages."""

    client: object
    chat_id: int
    message: object

    async def respond(self, text: str, reply_to: int | None = None):
        await self.client.send_message(self.chat_id, text, reply_to=reply_to)


async def _process_new_message_event(
    *,
    event,
    listener: AdbotListener,
    source_chat_ids: set[int] | None,
    enabled: bool,
) -> bool:
    """Process one Telegram event and return True if delegated to listener."""
    if source_chat_ids and event.chat_id not in source_chat_ids:
        message_obj = event.message if hasattr(event, "message") else None
        text = (getattr(message_obj, "text", "") or "").strip()
        sender_id = getattr(message_obj, "sender_id", None)
        try:
            log_decision(
                build_decision_payload(
                    chat_id=int(event.chat_id),
                    user_id=int(sender_id) if sender_id else None,
                    reason="source_chat_not_allowed",
                    message_text=text,
                )
            )
        except Exception:
            logger.exception("failed to log source filter decision")
        return False

    # Test mode can optionally disable source filtering for QA.
    if not enabled:
        message_obj = event.message if hasattr(event, "message") else None
        text = (getattr(message_obj, "text", "") or "").strip()
        sender_id = getattr(message_obj, "sender_id", None)
        try:
            log_decision(
                build_decision_payload(
                    chat_id=int(event.chat_id),
                    user_id=int(sender_id) if sender_id else None,
                    reason="adbot_disabled",
                    message_text=text,
                )
            )
        except Exception:
            logger.exception("failed to log disabled-state decision")
        return False

    await listener.process(event, source_chat_id=event.chat_id)
    return True


async def _safe_process_new_message_event(
    *,
    event,
    listener: AdbotListener,
    source_chat_ids: set[int] | None,
    enabled: bool,
) -> bool:
    """Safely process one Telegram event with structured exception logging."""
    try:
        return await _process_new_message_event(
            event=event,
            listener=listener,
            source_chat_ids=source_chat_ids,
            enabled=enabled,
        )
    except Exception:
        message_obj = event.message if hasattr(event, "message") else None
        text = (getattr(message_obj, "text", "") or "").strip()
        sender_id = getattr(message_obj, "sender_id", None)
        try:
            log_decision(
                build_decision_payload(
                    chat_id=int(event.chat_id),
                    user_id=int(sender_id) if sender_id else None,
                    reason="listener_exception",
                    message_text=text,
                )
            )
        except Exception:
            logger.exception("failed to log listener-exception decision")
        logger.exception("adbot listener error")
        return False


async def _run_self_outgoing_poll_loop(
    *,
    client,
    listener: AdbotListener,
    source_chat_ids: set[int],
    enabled: bool,
    self_user_id: int,
    prefix: str,
    poll_sec: float = 1.5,
) -> None:
    """Fallback polling loop for same-session E2E messages.

    Telegram may not deliver outgoing messages from another session as NewMessage
    updates to this session. In test-only self-outgoing E2E mode we poll source
    chats and process only prefixed outgoing self-messages.
    """
    if not source_chat_ids:
        return
    poll_interval = max(float(poll_sec), 0.5)
    last_seen: dict[int, int] = {}

    for chat_id in source_chat_ids:
        try:
            latest = await client.get_messages(chat_id, limit=1)
        except Exception:
            logger.exception("adbot poll baseline failed for chat_id=%s", chat_id)
            last_seen[chat_id] = 0
            continue
        if latest:
            last_seen[chat_id] = int(getattr(latest[0], "id", 0) or 0)
        else:
            last_seen[chat_id] = 0

    logger.info(
        "adbot self-outgoing poll started. chats=%s prefix=%s interval=%.2fs",
        sorted(source_chat_ids),
        prefix,
        poll_interval,
    )

    while True:
        for chat_id in source_chat_ids:
            try:
                batch = await client.get_messages(chat_id, limit=30)
            except Exception:
                logger.exception("adbot poll read failed for chat_id=%s", chat_id)
                continue
            cursor = int(last_seen.get(chat_id, 0) or 0)
            for message in reversed(batch):
                message_id = int(getattr(message, "id", 0) or 0)
                if message_id <= cursor:
                    continue
                cursor = max(cursor, message_id)
                sender_id = int(getattr(message, "sender_id", 0) or 0)
                is_outgoing = bool(getattr(message, "out", False))
                if not sender_id:
                    continue
                text = (
                    str(getattr(message, "text", "") or getattr(message, "raw_text", "") or "")
                    .strip()
                )
                if not text.startswith(prefix):
                    continue
                if is_outgoing and sender_id != self_user_id:
                    continue
                polled_event = _PolledMessageEvent(
                    client=client,
                    chat_id=int(chat_id),
                    message=message,
                )
                await _safe_process_new_message_event(
                    event=polled_event,
                    listener=listener,
                    source_chat_ids=source_chat_ids,
                    enabled=enabled,
                )
            last_seen[chat_id] = cursor
        await asyncio.sleep(poll_interval)


async def _run(config: AdbotConfig) -> None:
    try:
        from telethon import TelegramClient  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Telethon is required for adbot runtime. Ensure it is installed in runtime image."
        ) from exc

    from telethon import events  # type: ignore
    from telethon.sessions import StringSession  # type: ignore

    source_chat_ids = set(config.source_chat_ids) if config.source_chat_ids else None
    client = TelegramClient(StringSession(config.string_session), config.api_id, config.api_hash)

    await client.connect()
    if not await client.is_user_authorized():
        logger.error("adbot session is not authorized.")
        await client.disconnect()
        return
    me = await client.get_me()
    self_user_id = int(getattr(me, "id", 0) or 0) or None

    powerbot_inline = PowerbotInlineClient(client, config.target_powerbot_username)
    pipeline = ResponsePipeline(
        answer_provider=powerbot_inline,
        fallback_ms=config.pipeline_timeout_ms,
    )
    internal_pipeline = InternalReplyPipeline(
        tg_client=client,
        target_powerbot_username=config.target_powerbot_username,
        timeout_sec=config.internal_reply_timeout_sec,
        min_nonempty_len=config.internal_min_nonempty_len,
        require_real=config.internal_require_real_bot_reply,
        allowed_resident_bot_ids=config.internal_allowed_resident_bot_ids,
    )
    cooldown = CooldownGuard(config.reply_cooldown_sec)
    listener = AdbotListener(
        matcher_min_len=config.min_message_len,
        matcher_max_len=config.max_message_len,
        matcher_min_confidence=config.min_confidence,
        cooldown=cooldown,
        pipeline=pipeline,
        internal_pipeline=internal_pipeline,
        internal_chat_id=config.internal_chat_id,
        light_chat_bindings=config.light_chat_bindings,
        require_real_internal_reply=config.internal_require_real_bot_reply,
        allow_text_fallback_on_forward_failure=config.source_allow_text_fallback_on_forward_failure,
        allow_self_outgoing_e2e=config.allow_self_outgoing_e2e,
        self_user_id=self_user_id,
        self_outgoing_prefix=config.self_outgoing_prefix,
    )

    @client.on(events.NewMessage)
    async def on_new_message(event):
        await _safe_process_new_message_event(
            event=event,
            listener=listener,
            source_chat_ids=source_chat_ids,
            enabled=config.enabled,
        )

    poll_task: asyncio.Task | None = None
    if config.allow_self_outgoing_e2e and source_chat_ids and self_user_id:
        poll_task = asyncio.create_task(
            _run_self_outgoing_poll_loop(
                client=client,
                listener=listener,
                source_chat_ids=source_chat_ids,
                enabled=config.enabled,
                self_user_id=int(self_user_id),
                prefix=config.self_outgoing_prefix,
                poll_sec=config.self_outgoing_poll_sec,
            ),
            name="adbot-self-outgoing-poll",
        )

    logger.info(
        "adbot started. source_chats=%s self_user_id=%s allow_self_outgoing_e2e=%s",
        sorted(source_chat_ids) if source_chat_ids is not None else "<all>",
        self_user_id,
        config.allow_self_outgoing_e2e,
    )
    try:
        await client.run_until_disconnected()
    finally:
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("failed to stop adbot self-outgoing poll task")
        await client.disconnect()


def main() -> int:
    from logging_setup import configure_logging

    configure_logging("adbot")
    configure_decision_logging()
    if os.getenv("ADBOT_ENABLED", "0").strip() not in {"1", "true", "yes", "on"}:
        logger.info("ADBOT_ENABLED=0; skipping.")
        return 0
    try:
        config = build_config()
    except Exception as exc:
        logger.error("ADBOT config error: %s", exc)
        return 1

    try:
        asyncio.run(_run(config))
        return 0
    except Exception:
        logger.exception("adbot failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
