"""UI helpers for admin bot.

The admin bot is designed to work in "single message" mode by default:
all navigation updates edit one persistent inline-menu message, while user
input messages are best-effort deleted to keep the chat clean.
"""

from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from config import CFG
from database import db_get, db_set

logger = logging.getLogger(__name__)

_KV_KEY_LAST_UI_MESSAGE = "admin_ui:last_message_id:{chat_id}"


def _kv_key(chat_id: int) -> str:
    return _KV_KEY_LAST_UI_MESSAGE.format(chat_id=int(chat_id))


def escape(text: str) -> str:
    return html.escape(text or "")


async def bind_ui_message_id(chat_id: int, message_id: int) -> None:
    if not chat_id or not message_id:
        return
    try:
        await db_set(_kv_key(chat_id), str(int(message_id)))
    except Exception:
        logger.exception("Failed to bind admin ui message id for chat %s", chat_id)


async def get_ui_message_id(chat_id: int) -> int | None:
    if not chat_id:
        return None
    try:
        raw = await db_get(_kv_key(chat_id))
    except Exception:
        logger.exception("Failed to load admin ui message id for chat %s", chat_id)
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
        logger.exception("Failed to edit admin ui message chat=%s msg=%s", chat_id, message_id)
        return False


async def render(
    bot: Bot,
    *,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    prefer_message_id: int | None = None,
    disable_web_page_preview: bool = True,
    remove_reply_keyboard: bool = True,
    force_new_message: bool = False,
) -> int:
    """Render (send or edit) the admin bot UI message; returns message id."""
    if not chat_id:
        raise ValueError("chat_id is required")

    async def _remove_legacy_reply_keyboard() -> None:
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

    single_mode = bool(CFG.admin_bot_single_message_mode)
    if not single_mode:
        if remove_reply_keyboard:
            await _remove_legacy_reply_keyboard()
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return int(msg.message_id)

    if force_new_message:
        last_id = await get_ui_message_id(chat_id)
        if remove_reply_keyboard:
            await _remove_legacy_reply_keyboard()
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        new_id = int(msg.message_id)
        await bind_ui_message_id(chat_id, new_id)
        if last_id and int(last_id) != new_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=int(last_id))
            except Exception:
                pass
        return new_id

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
    """Best-effort delete user inputs in admin single-message UI."""
    if not CFG.admin_bot_single_message_mode:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    try:
        await message.delete()
    except Exception:
        pass

