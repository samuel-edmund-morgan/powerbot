"""UI helpers for business bot.

The business bot is designed to optionally work in "single message" mode:
all navigation updates edit one persistent inline-menu message, while user
input messages are best-effort deleted to keep the chat clean.

We intentionally DO NOT reuse `SingleMessageBot` from `src/single_message_bot.py`
because that implementation also touches main-bot notification records
(`active_notifications`) and uses a shared `last_bot_message` table keyed only
by `chat_id`. The business bot must not interfere with the main bot.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from config import CFG
from database import db_get, db_set


logger = logging.getLogger(__name__)

_KV_KEY_LAST_UI_MESSAGE = "business_ui:last_message_id:{chat_id}"


def _kv_key(chat_id: int) -> str:
    return _KV_KEY_LAST_UI_MESSAGE.format(chat_id=int(chat_id))


async def bind_ui_message_id(chat_id: int, message_id: int) -> None:
    """Persist the current UI message id for chat.

    Useful in callback handlers where we already know the message to edit.
    """
    if not chat_id or not message_id:
        return
    try:
        await db_set(_kv_key(chat_id), str(int(message_id)))
    except Exception:
        logger.exception("Failed to bind business ui message id for chat %s", chat_id)


async def get_ui_message_id(chat_id: int) -> int | None:
    if not chat_id:
        return None
    try:
        raw = await db_get(_kv_key(chat_id))
    except Exception:
        logger.exception("Failed to load business ui message id for chat %s", chat_id)
        return None
    if not raw:
        return None
    try:
        value = int(str(raw).strip())
    except Exception:
        return None
    return value if value > 0 else None


async def _try_edit(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    disable_web_page_preview: bool,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return True
    except TelegramBadRequest as exc:
        # Common: nothing changed. Still consider it "rendered".
        if "message is not modified" in str(exc).lower():
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                )
            except Exception:
                pass
            return True
        return False
    except Exception:
        logger.exception("Failed to edit business ui message chat=%s msg=%s", chat_id, message_id)
        return False


async def render(
    bot: Bot,
    *,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    prefer_message_id: int | None = None,
    disable_web_page_preview: bool = True,
    remove_reply_keyboard: bool = False,
) -> int:
    """Render (send or edit) the business bot UI message.

    Returns the message id of the UI message.
    """
    if not chat_id:
        raise ValueError("chat_id is required")

    async def _remove_legacy_reply_keyboard() -> None:
        # Telegram has no "remove reply keyboard" call; it happens only via a message.
        # We send a tiny message with ReplyKeyboardRemove and delete it immediately,
        # so the chat stays clean and the actual UI message can still carry an inline keyboard.
        try:
            tmp = await bot.send_message(
                chat_id=chat_id,
                text="…",
                reply_markup=ReplyKeyboardRemove(),
                disable_web_page_preview=True,
            )
        except Exception:
            return
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(tmp.message_id))
        except Exception:
            pass

    if not CFG.single_message_mode:
        if remove_reply_keyboard:
            await _remove_legacy_reply_keyboard()
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return int(msg.message_id)

    # Prefer editing the message we just received a callback for.
    if prefer_message_id:
        ok = await _try_edit(
            bot,
            chat_id=chat_id,
            message_id=int(prefer_message_id),
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        if ok:
            await bind_ui_message_id(chat_id, int(prefer_message_id))
            return int(prefer_message_id)

    last_id = await get_ui_message_id(chat_id)
    if last_id:
        ok = await _try_edit(
            bot,
            chat_id=chat_id,
            message_id=int(last_id),
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        if ok:
            return int(last_id)

    # First UI message: optionally force-remove legacy ReplyKeyboard (best-effort).
    if remove_reply_keyboard:
        await _remove_legacy_reply_keyboard()
    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    await bind_ui_message_id(chat_id, int(msg.message_id))
    return int(msg.message_id)


async def try_delete_user_message(message: Message) -> None:
    """Best-effort delete user input in SINGLE_MESSAGE_MODE to keep chat clean."""
    if not CFG.single_message_mode:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    try:
        # In private chats bots can usually delete user messages; ignore failures.
        await message.delete()
    except Exception:
        pass
