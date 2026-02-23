"""Audit helpers for adbot matches and decisions."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)
decision_logger = logging.getLogger("adbot.decision")
_DECISION_HANDLER_MARKER = "_is_adbot_decision_handler"
_DEFAULT_DECISION_FILE_NAME = "adbot_decisions.log"
_DEFAULT_LOG_DIR = "/data/logs"
_DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_LOG_BACKUP_COUNT = 10


def _clean(value: str | None, default: str) -> str:
    if value is None:
        return default
    return value.strip().strip('"').strip("'") or default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        return default
    try:
        return int(cleaned)
    except ValueError:
        return default


def configure_decision_logging() -> None:
    """Attach separate rotating-file handler for adbot decision logs.

    Decision entries stay visible in the main adbot log via propagation,
    and are additionally persisted in a dedicated file.
    """
    for handler in decision_logger.handlers:
        if getattr(handler, _DECISION_HANDLER_MARKER, False):
            return

    explicit_path = _clean(os.getenv("ADBOT_DECISION_LOG_PATH"), "")
    log_dir = _clean(os.getenv("LOG_DIR"), _DEFAULT_LOG_DIR)
    file_name = _clean(os.getenv("ADBOT_DECISION_LOG_FILE_NAME"), _DEFAULT_DECISION_FILE_NAME)
    max_bytes = _parse_int(
        os.getenv("ADBOT_DECISION_LOG_MAX_BYTES"),
        _parse_int(os.getenv("LOG_MAX_BYTES"), _DEFAULT_LOG_MAX_BYTES),
    )
    backup_count = _parse_int(
        os.getenv("ADBOT_DECISION_LOG_BACKUP_COUNT"),
        _parse_int(os.getenv("LOG_BACKUP_COUNT"), _DEFAULT_LOG_BACKUP_COUNT),
    )

    try:
        if explicit_path:
            path = Path(explicit_path)
            if path.parent:
                path.parent.mkdir(parents=True, exist_ok=True)
        else:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            path = Path(log_dir) / file_name
        handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
        setattr(handler, _DECISION_HANDLER_MARKER, True)
        decision_logger.addHandler(handler)
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True
    except Exception:
        logger.exception("failed to configure adbot decision logger")


def build_audit_payload(chat_id: int, user_id: int, intent_code: str, message_text: str) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "user_id": user_id,
        "intent": intent_code,
        "message": (message_text or "").strip()[:200],
    }


def build_decision_payload(
    *,
    chat_id: int,
    user_id: int | None,
    reason: str,
    message_text: str | None = None,
    intent_code: str | None = None,
    meta: dict | None = None,
) -> dict:
    payload: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": int(chat_id),
        "reason": str(reason or "").strip() or "unknown",
    }
    if user_id is not None:
        payload["user_id"] = int(user_id)
    if intent_code:
        payload["intent"] = str(intent_code).strip()
    if message_text:
        payload["message"] = str(message_text).strip()[:200]
    if meta:
        sanitized: dict[str, str | int | float | bool] = {}
        for key, value in meta.items():
            if value is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            if isinstance(value, bool):
                sanitized[key_str] = value
            elif isinstance(value, int):
                sanitized[key_str] = value
            elif isinstance(value, float):
                sanitized[key_str] = value
            else:
                sanitized[key_str] = str(value)[:120]
        if sanitized:
            payload["meta"] = sanitized
    return payload


def log_decision(payload: dict) -> None:
    """Write a structured adbot decision log entry."""
    decision_logger.info("adbot decision: %s", json.dumps(payload, ensure_ascii=False))


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
