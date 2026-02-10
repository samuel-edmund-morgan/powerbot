import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import CFG
from database import (
    create_admin_job,
    db_get,
    get_all_active_sensors,
    get_sensor_by_uuid,
    freeze_sensor,
    unfreeze_sensor,
    get_building_section_power_state,
    count_subscribers,
    get_subscribers_stats_by_building_section,
    list_admin_jobs,
    get_building_by_id,
    default_section_for_building,
)
from admin.ui import escape, render, try_delete_user_message

logger = logging.getLogger(__name__)
router = Router()

JOBS_PAGE_SIZE = 10
SENSORS_PAGE_SIZE = 8


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
    await _render_sensors_page(
        callback.bot,
        callback.message.chat.id,
        offset=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("admin_sensors_page|"))
async def cb_sensors_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        offset = int(callback.data.split("|", 1)[1])
    except Exception:
        offset = 0
    await _render_sensors_page(
        callback.bot,
        callback.message.chat.id,
        offset=max(0, offset),
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("admin_sensor|"))
async def cb_sensor(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    uuid = callback.data.split("|", 1)[1]
    await _render_sensor_detail(
        callback.bot,
        callback.message.chat.id,
        uuid=uuid,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("admin_sensor_freeze|"))
async def cb_sensor_freeze(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = callback.data.split("|")
    if len(parts) != 3:
        await callback.answer("❌ Некоректна команда", show_alert=True)
        return

    uuid = parts[1]
    try:
        seconds = int(parts[2])
    except Exception:
        await callback.answer("❌ Некоректна тривалість", show_alert=True)
        return

    # Safety bounds: prevent accidental huge freeze values.
    if seconds < 60 or seconds > 7 * 24 * 3600:
        await callback.answer("❌ Некоректна тривалість", show_alert=True)
        return

    sensor = await get_sensor_by_uuid(uuid)
    if not sensor:
        await callback.answer("❌ Сенсор не знайдено", show_alert=True)
        await _render_sensors_page(callback.bot, callback.message.chat.id, offset=0, prefer_message_id=callback.message.message_id)
        return

    bid = int(sensor["building_id"])
    sid = sensor.get("section_id") or default_section_for_building(bid) or 1
    sid = int(sid)

    # Freeze should keep SECTION state stable, so we snapshot current section power state.
    section_state = await get_building_section_power_state(bid, sid)
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    if section_state is not None:
        frozen_is_up = bool(section_state["is_up"])
    else:
        frozen_is_up = bool(sensor.get("last_heartbeat") and (now - sensor["last_heartbeat"]) < timeout)

    ok = await freeze_sensor(
        uuid,
        frozen_until=now + timedelta(seconds=seconds),
        frozen_is_up=frozen_is_up,
        frozen_at=now,
    )
    if not ok:
        await callback.answer("❌ Не вдалося заморозити", show_alert=True)
    else:
        await callback.answer("🧊 Заморожено")

    await _render_sensor_detail(
        callback.bot,
        callback.message.chat.id,
        uuid=uuid,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("admin_sensor_unfreeze|"))
async def cb_sensor_unfreeze(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    uuid = callback.data.split("|", 1)[1]
    ok = await unfreeze_sensor(uuid)
    if not ok:
        await callback.answer("❌ Не вдалося розморозити", show_alert=True)
    else:
        await callback.answer("✅ Розморожено")

    await _render_sensor_detail(
        callback.bot,
        callback.message.chat.id,
        uuid=uuid,
        prefer_message_id=callback.message.message_id,
    )


async def _render_sensors_page(bot: Bot, chat_id: int, *, offset: int, prefer_message_id: int | None) -> None:
    sensors = await get_all_active_sensors()
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)

    def _sort_key(s: dict) -> tuple:
        bid = int(s.get("building_id") or 0)
        sid = s.get("section_id") or default_section_for_building(bid) or 0
        return (bid, int(sid), str(s.get("uuid") or ""))

    sensors.sort(key=_sort_key)

    total = len(sensors)
    if total == 0:
        text = "📡 <b>Сенсори</b>\n\nНемає активних сенсорів."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")]])
        await render(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    if offset >= total:
        offset = max(0, total - (total % SENSORS_PAGE_SIZE or SENSORS_PAGE_SIZE))

    page = sensors[offset : offset + SENSORS_PAGE_SIZE]
    text = (
        "📡 <b>Сенсори</b>\n\n"
        f"Показано: <b>{offset + 1}..{offset + len(page)}</b> з <b>{total}</b>\n"
        "Натисніть сенсор, щоб відкрити.\n\n"
        "Позначки: 🧊 = заморожено (щоб прошивати без фейкових сповіщень)."
    )

    rows: list[list[InlineKeyboardButton]] = []
    for s in page:
        bid = int(s["building_id"])
        building = get_building_by_id(bid)
        bname = building["name"] if building else f"ID:{bid}"
        sid = s.get("section_id") or default_section_for_building(bid) or "—"
        frozen_until = s.get("frozen_until")
        frozen_active = bool(frozen_until and frozen_until > now)

        if s.get("last_heartbeat"):
            age = now - s["last_heartbeat"]
            online = age < timeout
            status_icon = "🟢" if online else "🔴"
        else:
            status_icon = "⚪"

        freeze_icon = "🧊" if frozen_active else ""
        short_uuid = str(s["uuid"])[:12]
        btn_text = f"{status_icon}{freeze_icon} {bname} s{sid} • {short_uuid}"
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"admin_sensor|{s['uuid']}")])

    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Новіші",
                callback_data=f"admin_sensors_page|{max(0, offset - SENSORS_PAGE_SIZE)}",
            )
        )
    if offset + SENSORS_PAGE_SIZE < total:
        nav.append(
            InlineKeyboardButton(
                text="Старіші ➡️",
                callback_data=f"admin_sensors_page|{offset + SENSORS_PAGE_SIZE}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 Оновити", callback_data=f"admin_sensors_page|{offset}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


async def _render_sensor_detail(bot: Bot, chat_id: int, *, uuid: str, prefer_message_id: int | None) -> None:
    sensor = await get_sensor_by_uuid(uuid)
    if not sensor:
        text = "📡 <b>Сенсор</b>\n\n❌ Сенсор не знайдено."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 До сенсорів", callback_data="admin_sensors")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")],
            ]
        )
        await render(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    bid = int(sensor["building_id"])
    building = get_building_by_id(bid)
    bname = building["name"] if building else f"ID:{bid}"
    baddr = building.get("address") if building else None
    sid = sensor.get("section_id") or default_section_for_building(bid) or "—"
    comment = (sensor.get("comment") or "").strip()

    # Real online status based on heartbeat.
    if sensor.get("last_heartbeat"):
        age = now - sensor["last_heartbeat"]
        online = age < timeout
        status = "🟢 online" if online else "🔴 offline"
        when = (
            f"{int(age.total_seconds())} сек тому"
            if age.total_seconds() < 60
            else sensor["last_heartbeat"].strftime("%d.%m %H:%M")
        )
    else:
        status = "⚪ unknown"
        when = "ніколи"

    frozen_until = sensor.get("frozen_until")
    frozen_active = bool(frozen_until and frozen_until > now)
    frozen_is_up = sensor.get("frozen_is_up")

    title = f"{bname} секція {sid}"
    if baddr:
        title = f"{bname} ({baddr}) секція {sid}"

    text = f"📡 <b>Сенсор</b>\n\n🏠 <b>{escape(title)}</b>\n"
    text += f"{status} • {escape(when)}\n"
    text += f"uuid: <code>{escape(str(sensor['uuid']))}</code>\n"
    if comment:
        text += f"comment: <code>{escape(comment)}</code>\n"

    if frozen_active:
        until_str = frozen_until.strftime("%d.%m %H:%M")
        if frozen_is_up is True:
            eff = "✅ UP"
        elif frozen_is_up is False:
            eff = "❌ DOWN"
        else:
            eff = "⚪ unknown"
        text += (
            "\n🧊 <b>Заморожено</b>\n"
            f"до: <b>{escape(until_str)}</b>\n"
            f"поки заморожено: секція рахується як <b>{escape(eff)}</b>\n"
        )
    elif frozen_until:
        # Expired freeze (left in DB until explicit unfreeze).
        until_str = frozen_until.strftime("%d.%m %H:%M")
        text += f"\n🧊 Заморозка завершилась: <b>{escape(until_str)}</b>\n"

    rows: list[list[InlineKeyboardButton]] = []
    if frozen_active or frozen_until:
        rows.append([InlineKeyboardButton(text="✅ Розморозити", callback_data=f"admin_sensor_unfreeze|{uuid}")])
        # Quick extend options (keeps current frozen_is_up snapshot).
        rows.append(
            [
                InlineKeyboardButton(text="🧊 +15 хв", callback_data=f"admin_sensor_freeze|{uuid}|900"),
                InlineKeyboardButton(text="🧊 +1 год", callback_data=f"admin_sensor_freeze|{uuid}|3600"),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(text="🧊 15 хв", callback_data=f"admin_sensor_freeze|{uuid}|900"),
                InlineKeyboardButton(text="🧊 1 год", callback_data=f"admin_sensor_freeze|{uuid}|3600"),
                InlineKeyboardButton(text="🧊 6 год", callback_data=f"admin_sensor_freeze|{uuid}|21600"),
            ]
        )

    rows.append([InlineKeyboardButton(text="🔄 Оновити", callback_data=f"admin_sensor|{uuid}")])
    rows.append([InlineKeyboardButton(text="🔙 До сенсорів", callback_data="admin_sensors")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == "admin_subs")
async def cb_subs(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()

    total = await count_subscribers()
    stats = await get_subscribers_stats_by_building_section()

    text = f"👥 <b>Підписники</b>\n\nВсього: <b>{total}</b>\n\n"

    # Build totals per building for sorting.
    building_totals: list[tuple[int | None, int]] = []
    for bid, by_section in stats.items():
        building_totals.append((bid, sum(int(v) for v in by_section.values())))
    building_totals.sort(key=lambda x: (-x[1], x[0] is None, x[0] or 0))

    if not building_totals:
        text += "Немає підписників."
    else:
        text += "<b>По будинках / секціях:</b>\n"
        for bid, b_total in building_totals:
            if bid is None:
                text += f"\n• <b>Без будинку</b>: {b_total}\n"
                continue

            building = get_building_by_id(int(bid))
            bname = building["name"] if building else f"ID:{bid}"
            text += f"\n• <b>{escape(bname)}</b>: {b_total}\n"

            by_section = stats.get(bid) or {}
            # Prefer stable order 1..3; keep legacy NULL at the end.
            for sid in [1, 2, 3]:
                if sid in by_section:
                    text += f"  секція {sid}: {int(by_section[sid])}\n"
            if None in by_section:
                text += f"  без секції: {int(by_section[None])}\n"

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
    await _render_jobs_page(
        callback.bot,
        callback.message.chat.id,
        offset=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("admin_jobs_page|"))
async def cb_jobs_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        offset = int(callback.data.split("|", 1)[1])
    except Exception:
        offset = 0
    await _render_jobs_page(
        callback.bot,
        callback.message.chat.id,
        offset=max(0, offset),
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == "admin_jobs_export")
async def cb_jobs_export(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Готую файл…")

    jobs = await list_admin_jobs(limit=5000, offset=0)
    if not jobs:
        await callback.answer("Порожньо", show_alert=True)
        return

    lines = ["#id\tkind\tstatus\tcreated_at\tprogress\tlast_error"]
    for j in jobs:
        progress = ""
        pt = j.get("progress_total") or 0
        if pt:
            progress = f"{j.get('progress_current') or 0}/{pt}"
        err = (j.get("last_error") or "").replace("\n", " ").strip()
        if len(err) > 200:
            err = err[:197] + "..."
        lines.append(
            f"{j['id']}\t{j['kind']}\t{j['status']}\t{j.get('created_at') or ''}\t{progress}\t{err}"
        )

    file_content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(file_content, filename="admin_jobs.txt")
    await callback.message.answer_document(
        file,
        caption=f"🧾 <b>Admin jobs</b>\nРядків: <b>{len(jobs)}</b>",
    )


async def _render_jobs_page(bot: Bot, chat_id: int, *, offset: int, prefer_message_id: int | None) -> None:
    jobs = await list_admin_jobs(limit=JOBS_PAGE_SIZE, offset=offset)
    if not jobs and offset > 0:
        # If user paged too far, snap back to the first page.
        offset = 0
        jobs = await list_admin_jobs(limit=JOBS_PAGE_SIZE, offset=offset)

    if not jobs:
        text = "🧾 <b>Черга задач</b>\n\nПорожньо."
    else:
        text = f"🧾 <b>Черга задач</b>\n\nПоказано: <b>{offset + 1}..{offset + len(jobs)}</b>\n\n"
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

    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Новіші",
                callback_data=f"admin_jobs_page|{max(0, offset - JOBS_PAGE_SIZE)}",
            )
        )
    if len(jobs) == JOBS_PAGE_SIZE:
        nav.append(
            InlineKeyboardButton(
                text="Старіші ➡️",
                callback_data=f"admin_jobs_page|{offset + JOBS_PAGE_SIZE}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 Оновити", callback_data=f"admin_jobs_page|{offset}")])
    rows.append([InlineKeyboardButton(text="📄 Експорт (файл)", callback_data="admin_jobs_export")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )
