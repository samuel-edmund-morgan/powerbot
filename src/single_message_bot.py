import logging
from typing import Any

from aiogram import Bot

from database import (
    get_active_notifications_for_chat,
    delete_notification,
    get_last_bot_message,
    delete_last_bot_message_record,
    save_last_bot_message,
)


async def _cleanup_chat(bot: Bot, chat_id: int) -> None:
    """Delete any previous bot messages and active notifications for this chat."""
    if not chat_id:
        return

    try:
        notifications = await get_active_notifications_for_chat(chat_id)
    except Exception:
        logging.exception("Failed to load active notifications for chat %s", chat_id)
        notifications = []

    for notif in notifications:
        try:
            await bot.delete_message(chat_id, notif["message_id"])
        except Exception:
            pass
        try:
            await delete_notification(notif["id"])
        except Exception:
            pass

    try:
        last_id = await get_last_bot_message(chat_id)
    except Exception:
        logging.exception("Failed to load last_bot_message for chat %s", chat_id)
        last_id = None

    if last_id:
        try:
            await bot.delete_message(chat_id, last_id)
        except Exception:
            pass
        try:
            await delete_last_bot_message_record(chat_id)
        except Exception:
            pass


class SingleMessageBot(Bot):
    """Bot that enforces a single visible message per chat."""

    async def _cleanup_before_send(self, chat_id: int) -> None:
        await _cleanup_chat(self, chat_id)

    async def send_message(self, chat_id: int, text: str, **kwargs: Any):
        await self._cleanup_before_send(chat_id)
        msg = await super().send_message(chat_id, text, **kwargs)
        try:
            await save_last_bot_message(chat_id, msg.message_id)
        except Exception:
            logging.exception("Failed to save last_bot_message for chat %s", chat_id)
        return msg

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any):
        await self._cleanup_before_send(chat_id)
        msg = await super().send_photo(chat_id, photo, **kwargs)
        try:
            await save_last_bot_message(chat_id, msg.message_id)
        except Exception:
            logging.exception("Failed to save last_bot_message for chat %s", chat_id)
        return msg

    async def send_document(self, chat_id: int, document: Any, **kwargs: Any):
        await self._cleanup_before_send(chat_id)
        msg = await super().send_document(chat_id, document, **kwargs)
        try:
            await save_last_bot_message(chat_id, msg.message_id)
        except Exception:
            logging.exception("Failed to save last_bot_message for chat %s", chat_id)
        return msg
