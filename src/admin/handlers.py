import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import CFG
from database import (
    create_admin_job,
    db_get,
    get_all_active_sensors,
    count_subscribers,
    list_admin_jobs,
    get_building_by_id,
)
from admin.ui import escape, render, try_delete_user_message

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return int(user_id) in set(CFG.admin_ids)


async def _require_admin_message(message: Message) -> bool:
    if not message.from_user or not is_admin(message.from_user.id):
        try:
            await message.answer("❌ Доступно лише адміністраторам.")
        except Exception:
            pass
        return False
    return True


async def _require_admin_callback(callback: CallbackQuery) -> bool:
    if not callback.from_user or not is_admin(callback.from_user.id):
        try:
            await callback.answer("❌ Лише для адмінів", show_alert=True)
        except Exception:
            pass
        return False
    return True


def _menu_keyboard(light_enabled: bool) -> InlineKeyboardMarkup:
    light_label = "🟢 Сповіщення світла: ON" if light_enabled else "🔴 Сповіщення світла: OFF"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=light_label, callback_data="admin_toggle_light")],
            [InlineKeyboardButton(text="📣 Розсилка (broadcast)", callback_data="admin_broadcast")],
            [
                InlineKeyboardButton(text="📡 Сенсори", callback_data="admin_sensors"),
                InlineKeyboardButton(text="👥 Підписники", callback_data="admin_subs"),
            ],
            [InlineKeyboardButton(text="🧾 Черга задач", callback_data="admin_jobs")],
            [InlineKeyboardButton(text="🔄 Оновити", callback_data="admin_refresh")],
        ]
    )


async def _get_light_enabled() -> bool:
    val = await db_get("light_notifications_global")
    return val != "off"


async def _render_main_menu(bot, chat_id: int, *, prefer_message_id: int | None = None, note: str | None = None) -> None:
    light_enabled = await _get_light_enabled()
    text = "🔧 <b>Адмін‑бот</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть дію:"
    kb = _menu_keyboard(light_enabled)
    await render(bot, chat_id=chat_id, text=text, reply_markup=kb, prefer_message_id=prefer_message_id, force_new_message=True)


class BroadcastState(StatesGroup):
    waiting_text = State()
    confirm = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await state.clear()
    await try_delete_user_message(message)
    await _render_main_menu(message.bot, message.chat.id, note=None)


@router.callback_query(F.data == "admin_refresh")
async def cb_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await _render_main_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)


@router.callback_query(F.data == "admin_toggle_light")
async def cb_toggle_light(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Ставлю в чергу…")
    current = await _get_light_enabled()
    desired = "off" if current else "on"
    job_id = await create_admin_job(
        "light_notify",
        {"value": desired},
        created_by=int(callback.from_user.id),
    )
    note = f"✅ Додано в чергу: <b>light_notify={desired}</b>\nJob: <code>#{job_id}</code>"
    await _render_main_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id, note=note)


@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.set_state(BroadcastState.waiting_text)
    await callback.answer()
    text = (
        "📣 <b>Розсилка</b>\n\n"
        "Надішліть текст, який потрібно відправити всім підписникам.\n"
        "Текст буде надіслано як plain-text (без розмітки).\n\n"
        "Або натисніть «Скасувати»."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
        ]
    )
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.message(BroadcastState.waiting_text)
async def msg_broadcast_text(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    text = (message.text or "").strip()
    await try_delete_user_message(message)
    if not text:
        await _render_main_menu(message.bot, message.chat.id, note="❌ Порожній текст. Спробуйте ще раз.")
        await state.clear()
        return

    await state.update_data(broadcast_text=text)
    await state.set_state(BroadcastState.confirm)

    preview = escape(text)
    ui_text = (
        "📣 <b>Підтвердіть розсилку</b>\n\n"
        "Ось як виглядатиме повідомлення:\n\n"
        f"<code>{preview}</code>\n\n"
        "Натисніть «Запустити» щоб поставити задачу в чергу."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запустити", callback_data="admin_broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
        ]
    )
    await render(message.bot, chat_id=message.chat.id, text=ui_text, reply_markup=kb, force_new_message=True)


@router.callback_query(F.data == "admin_broadcast_confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    await state.clear()
    if not text:
        await callback.answer("❌ Немає тексту", show_alert=True)
        await _render_main_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)
        return

    await callback.answer("⏳ Додаю в чергу…")
    job_id = await create_admin_job(
        "broadcast",
        {"text": text, "prefix": "📢 "},
        created_by=int(callback.from_user.id),
    )
    note = f"✅ Розсилка поставлена в чергу.\nJob: <code>#{job_id}</code>"
    await _render_main_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id, note=note)


@router.callback_query(F.data == "admin_cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer("Скасовано")
    await _render_main_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)


@router.callback_query(F.data == "admin_sensors")
async def cb_sensors(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()

    sensors = await get_all_active_sensors()
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)

    if not sensors:
        text = "📡 <b>Сенсори</b>\n\nНемає активних сенсорів."
    else:
        text = "📡 <b>Сенсори</b>\n\n"
        for s in sensors:
            building = get_building_by_id(int(s["building_id"]))
            bname = building["name"] if building else f"ID:{s['building_id']}"
            sid = s.get("section_id") or "—"
            comment = (s.get("comment") or "").strip()
            if s.get("last_heartbeat"):
                age = now - s["last_heartbeat"]
                online = age < timeout
                status = "🟢 online" if online else "🔴 offline"
                when = (
                    f"{int(age.total_seconds())} сек тому"
                    if age.total_seconds() < 60
                    else s["last_heartbeat"].strftime("%d.%m %H:%M")
                )
            else:
                status = "⚪ unknown"
                when = "ніколи"

            name = (s.get("name") or "").strip() or s["uuid"][:12]
            text += f"<b>{bname}</b> секція {sid}\n"
            text += f"  {status} • {when}\n"
            text += f"  uuid: <code>{escape(s['uuid'])}</code>\n"
            if comment:
                text += f"  comment: <code>{escape(comment)}</code>\n"
            text += "\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")]])
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == "admin_subs")
async def cb_subs(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()

    total = await count_subscribers()
    text = f"👥 <b>Підписники</b>\n\nВсього: <b>{total}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")]])
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == "admin_jobs")
async def cb_jobs(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()

    jobs = await list_admin_jobs(limit=10, offset=0)
    if not jobs:
        text = "🧾 <b>Черга задач</b>\n\nПорожньо."
    else:
        text = "🧾 <b>Черга задач (останні 10)</b>\n\n"
        for j in jobs:
            jid = j["id"]
            status = j["status"]
            kind = j["kind"]
            created = j.get("created_at") or ""
            text += f"• <code>#{jid}</code> <b>{escape(kind)}</b> — <b>{escape(status)}</b>\n"
            if created:
                text += f"  <i>{escape(created)}</i>\n"
            if j.get("last_error"):
                err = str(j["last_error"])
                if len(err) > 120:
                    err = err[:117] + "..."
                text += f"  ❌ <code>{escape(err)}</code>\n"
            pc = j.get("progress_current") or 0
            pt = j.get("progress_total") or 0
            if pt:
                text += f"  прогрес: {pc}/{pt}\n"
            text += "\n"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Оновити", callback_data="admin_jobs")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")],
        ]
    )
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )

