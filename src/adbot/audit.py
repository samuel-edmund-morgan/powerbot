"""Audit helpers for adbot matches and decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_audit_payload(chat_id: int, user_id: int, intent_code: str, message_text: str) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "user_id": user_id,
        "intent": intent_code,
        "message": (message_text or "").strip()[:200],
    }


async def log_match(
    payload: dict,
    *,
    internal_chat_id: int | None = None,
    forwarder,
    original_message=None,
) -> None:
    logger.info("adbot match: %s", json.dumps(payload, ensure_ascii=False))
    if internal_chat_id and forwarder:
        try:
            # Forward the original user message first to keep audit context native.
            if original_message is not None and hasattr(forwarder, "forward_messages"):
                await forwarder.forward_messages(internal_chat_id, original_message)

            text = (
                "🔎 <b>adbot match</b>\n"
                f"chat_id: <code>{payload.get('chat_id')}</code>\n"
                f"user_id: <code>{payload.get('user_id')}</code>\n"
                f"intent: <code>{payload.get('intent')}</code>\n"
                f"message: {payload.get('message')}"
            )
            await forwarder.send_message(internal_chat_id, text)
        except Exception:
            logger.exception("failed to forward audit message to internal chat")
