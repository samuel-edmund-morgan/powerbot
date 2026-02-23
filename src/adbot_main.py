#!/usr/bin/env python3
"""Standalone adbot runtime (Telethon)."""

from __future__ import annotations

import asyncio
import logging
import os

from adbot.cooldown import CooldownGuard
from adbot.listener import AdbotListener
from adbot.pipeline import PowerbotInlineClient, ResponsePipeline
from adbot.audit import build_decision_payload, configure_decision_logging, log_decision
from adbot_main_config import AdbotConfig, build_config

logger = logging.getLogger(__name__)


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

    powerbot_inline = PowerbotInlineClient(client, config.target_powerbot_username)
    pipeline = ResponsePipeline(
        answer_provider=powerbot_inline,
        fallback_ms=config.pipeline_timeout_ms,
    )
    cooldown = CooldownGuard(config.reply_cooldown_sec)
    listener = AdbotListener(
        matcher_min_len=config.min_message_len,
        matcher_max_len=config.max_message_len,
        matcher_min_confidence=config.min_confidence,
        cooldown=cooldown,
        pipeline=pipeline,
        internal_chat_id=config.internal_chat_id,
    )

    @client.on(events.NewMessage)
    async def on_new_message(event):
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
            return

        # Test mode can optionally disable source filtering for QA.
        if not config.enabled:
            return

        try:
            await listener.process(event, source_chat_id=event.chat_id)
        except Exception:
            logger.exception("adbot listener error")

    logger.info(
        "adbot started. source_chats=%s",
        sorted(source_chat_ids) if source_chat_ids is not None else "<all>",
    )
    try:
        await client.run_until_disconnected()
    finally:
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
