import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
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
from tg_buttons import STYLE_DANGER, STYLE_SUCCESS, ikb
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
    get_building_section_ids,
    default_section_for_building,
    list_place_reports,
    set_place_report_status,
)
from admin.ui import escape, render, try_delete_user_message
from business.repository import BusinessRepository
from business.plans import PLAN_TITLES
from business.service import AccessDeniedError as BusinessAccessDeniedError
from business.service import BusinessCabinetService
from business.service import NotFoundError as BusinessNotFoundError
from business.service import ValidationError as BusinessValidationError
from business.ui import render as render_business_ui

logger = logging.getLogger(__name__)
router = Router()

JOBS_PAGE_SIZE = 10
SENSORS_PAGE_SIZE = 8
BIZ_SERVICES_PAGE_SIZE = 10
BIZ_PLACES_PAGE_SIZE = 8
BIZ_SUBS_PAGE_SIZE = 8
BIZ_PAYMENTS_PAGE_SIZE = 8
BIZ_AUDIT_PAGE_SIZE = 8
CB_ADMIN_NOOP = "admin_noop"
SENSORS_FREEZE_ALL_DEFAULT_SEC = 6 * 3600
SENSORS_FREEZE_FOREVER_TOKEN = "forever"
SENSORS_FREEZE_FOREVER_UNTIL = datetime(9999, 12, 31, 23, 59, 59)

CB_BIZ_MENU = "admin_business"
CB_BIZ_MOD = "abiz_mod"
CB_BIZ_MOD_PAGE_PREFIX = "abiz_mod_page|"
CB_BIZ_MOD_JUMP_PREFIX = "abiz_mod_jump|"
CB_BIZ_MOD_APPROVE_PREFIX = "abiz_mod_approve|"
CB_BIZ_MOD_REJECT_PREFIX = "abiz_mod_reject|"
CB_BIZ_REPORTS = "abiz_reports"
CB_BIZ_REPORTS_PAGE_PREFIX = "abiz_reports_page|"
CB_BIZ_REPORTS_JUMP_PREFIX = "abiz_reports_jump|"
CB_BIZ_REPORTS_RESOLVE_PREFIX = "abiz_reports_resolve|"

CB_BIZ_TOK_MENU = "abiz_tok_menu"
CB_BIZ_TOK_LIST = "abiz_tok_list"
CB_BIZ_TOK_GEN = "abiz_tok_gen"
CB_BIZ_TOK_GEN_ALL = "abiz_tok_gen_all"
CB_BIZ_TOK_GEN_ALL_CONFIRM = "abiz_tok_gen_all_confirm"

CB_BIZ_TOKV_SVC_PAGE_PREFIX = "abiz_tokv_sp|"
CB_BIZ_TOKV_SVC_PICK_PREFIX = "abiz_tokv_s|"
CB_BIZ_TOKV_PLACE_PAGE_PREFIX = "abiz_tokv_pp|"
CB_BIZ_TOKV_PLACE_OPEN_PREFIX = "abiz_tokv_o|"
CB_BIZ_TOKV_PLACE_ROTATE_PREFIX = "abiz_tokv_r|"

CB_BIZ_TOKG_SVC_PAGE_PREFIX = "abiz_tokg_sp|"
CB_BIZ_TOKG_SVC_PICK_PREFIX = "abiz_tokg_s|"
CB_BIZ_TOKG_PLACE_PAGE_PREFIX = "abiz_tokg_pp|"
CB_BIZ_TOKG_PLACE_ROTATE_PREFIX = "abiz_tokg_r|"

CB_BIZ_SUBS = "abiz_subs"
CB_BIZ_SUBS_PAGE_PREFIX = "abiz_subs_page|"
CB_BIZ_SUBS_EXPORT = "abiz_subs_export"

CB_BIZ_PAYMENTS = "abiz_payments"
CB_BIZ_PAYMENTS_PAGE_PREFIX = "abiz_payments_page|"
CB_BIZ_PAYMENTS_EXPORT = "abiz_payments_export"
CB_BIZ_PAY_REFUND_PREFIX = "abiz_pay_refund|"
CB_BIZ_PAY_REFUND_CONFIRM_PREFIX = "abiz_pay_refundc|"

CB_BIZ_AUDIT = "abiz_audit"
CB_BIZ_AUDIT_PAGE_PREFIX = "abiz_audit_page|"

CB_BIZ_PLACES_MENU = "abiz_places"
CB_BIZ_PLACES_FILTER_PREFIX = "abiz_places_f|"
CB_BIZ_PLACES_SEARCH_PREFIX = "abiz_places_search|"
CB_BIZ_PLACES_SVC_PAGE_PREFIX = "abiz_places_sp|"
CB_BIZ_PLACES_SVC_PICK_PREFIX = "abiz_places_s|"
CB_BIZ_PLACES_PLACE_PAGE_PREFIX = "abiz_places_pp|"
CB_BIZ_PLACES_PLACE_OPEN_PREFIX = "abiz_places_o|"
CB_BIZ_PLACES_PUBLISH_PREFIX = "abiz_places_pub|"
CB_BIZ_PLACES_HIDE_PREFIX = "abiz_places_hide|"
CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX = "abiz_places_hidec|"
CB_BIZ_PLACES_DELETE_PREFIX = "abiz_places_del|"
CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX = "abiz_places_delc|"
CB_BIZ_PLACES_REJECT_OWNER_PREFIX = "abiz_places_ro|"
CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX = "abiz_places_roc|"
CB_BIZ_PLACES_EDIT_MENU_PREFIX = "abiz_places_edit|"
CB_BIZ_PLACES_EDIT_FIELD_PREFIX = "abiz_places_editf|"
CB_BIZ_PLACES_EDIT_BUILDING_PREFIX = "abiz_places_editb|"
CB_BIZ_PLACES_PROMO_MENU_PREFIX = "abiz_places_promo_m|"
CB_BIZ_PLACES_PROMO_SET_PREFIX = "abiz_places_promo_s|"

CB_BIZ_CATEGORIES_MENU = "abiz_categories"
CB_BIZ_CATEGORIES_PAGE_PREFIX = "abiz_categories_p|"
CB_BIZ_CATEGORY_OPEN_PREFIX = "abiz_category_o|"
CB_BIZ_CATEGORY_RENAME_PREFIX = "abiz_category_r|"
CB_BIZ_CATEGORY_ADD = "abiz_category_add"

CB_BIZ_CREATE_PLACE_MENU = "abiz_create_place"
CB_BIZ_CREATE_SVC_PAGE_PREFIX = "abiz_create_sp|"
CB_BIZ_CREATE_SVC_PICK_PREFIX = "abiz_create_s|"
CB_BIZ_CREATE_BUILDING_PICK_PREFIX = "abiz_create_b|"
CB_BIZ_CREATE_PROMO_PREFIX = "abiz_create_promo|"

business_service = BusinessCabinetService()
business_repo = BusinessRepository()


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


def _is_freeze_forever(frozen_until: datetime | None) -> bool:
    return bool(frozen_until and frozen_until.year >= 9999)


def _parse_freeze_token(raw_token: str) -> tuple[int | None, bool]:
    token = str(raw_token or "").strip().lower()
    if token == SENSORS_FREEZE_FOREVER_TOKEN:
        return None, True
    seconds = int(token)
    if seconds < 60 or seconds > 7 * 24 * 3600:
        raise ValueError("freeze seconds out of range")
    return seconds, False


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
            [InlineKeyboardButton(text="🏢 Бізнес", callback_data=CB_BIZ_MENU)],
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


class BizPlacesSearchState(StatesGroup):
    waiting_query = State()


class BizCategoryCreateState(StatesGroup):
    waiting_name = State()


class BizCategoryRenameState(StatesGroup):
    waiting_name = State()


class BizPlaceCreateState(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_address_details = State()


class BizPlaceEditState(StatesGroup):
    waiting_value = State()
    waiting_address_details = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject | None = None) -> None:
    if not await _require_admin_message(message):
        return
    await state.clear()
    await try_delete_user_message(message)
    args = str(command.args or "").strip() if command else ""
    if args.startswith("brep_"):
        try:
            report_id = int(args.split("_", 1)[1])
        except Exception:
            report_id = 0
        if report_id > 0:
            await _render_business_reports(
                message.bot,
                message.chat.id,
                index=0,
                report_id=report_id,
                prefer_message_id=None,
            )
            return
    if args.startswith("bmod_"):
        try:
            owner_id = int(args.split("_", 1)[1])
        except Exception:
            owner_id = 0
        if owner_id > 0:
            await _render_business_moderation(
                message.bot,
                message.chat.id,
                index=0,
                owner_id=owner_id,
                prefer_message_id=None,
            )
            return
    await _render_main_menu(message.bot, message.chat.id, note=None)


@router.callback_query(F.data == CB_ADMIN_NOOP)
async def cb_admin_noop(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()


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
        seconds, is_forever = _parse_freeze_token(parts[2])
    except Exception:
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
        frozen_until=(
            SENSORS_FREEZE_FOREVER_UNTIL
            if is_forever
            else (now + timedelta(seconds=int(seconds or 0)))
        ),
        frozen_is_up=frozen_is_up,
        frozen_at=now,
    )
    if not ok:
        await callback.answer("❌ Не вдалося заморозити", show_alert=True)
    else:
        await callback.answer("🧊 Заморожено до розморозки" if is_forever else "🧊 Заморожено")

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


async def _render_sensors_page(
    bot: Bot,
    chat_id: int,
    *,
    offset: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
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
    text = "📡 <b>Сенсори</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += (
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
    rows.append(
        [
            InlineKeyboardButton(
                text="🧊 Заморозити всі (6 год)",
                callback_data=f"admin_sensors_freeze_all|{SENSORS_FREEZE_ALL_DEFAULT_SEC}",
            ),
            InlineKeyboardButton(text="🧊 Всі до розморозки", callback_data=f"admin_sensors_freeze_all|{SENSORS_FREEZE_FOREVER_TOKEN}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="✅ Розморозити всі", callback_data="admin_sensors_unfreeze_all")])
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


@router.callback_query(F.data.startswith("admin_sensors_freeze_all|"))
async def cb_sensors_freeze_all(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        seconds, is_forever = _parse_freeze_token(callback.data.split("|", 1)[1])
    except Exception:
        await callback.answer("❌ Некоректна тривалість", show_alert=True)
        return

    await callback.answer("⏳ Ставлю в чергу…")
    payload: dict[str, int | str]
    if is_forever:
        payload = {"mode": SENSORS_FREEZE_FOREVER_TOKEN}
        duration_line = "Тривалість: <b>до ручної розморозки</b>"
    else:
        payload = {"seconds": int(seconds or SENSORS_FREEZE_ALL_DEFAULT_SEC)}
        hours = round((seconds or 0) / 3600, 2)
        duration_line = f"Тривалість: <b>{hours} год</b>"
    job_id = await create_admin_job(
        "sensors_freeze_all",
        payload,
        created_by=int(callback.from_user.id),
    )
    note = (
        "✅ Додано в чергу: <b>Заморозити всі сенсори</b>\n"
        f"{duration_line}\n"
        f"Job: <code>#{job_id}</code>"
    )
    await _render_sensors_page(
        callback.bot,
        callback.message.chat.id,
        offset=0,
        prefer_message_id=callback.message.message_id,
        note=note,
    )


@router.callback_query(F.data == "admin_sensors_unfreeze_all")
async def cb_sensors_unfreeze_all(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Ставлю в чергу…")
    job_id = await create_admin_job(
        "sensors_unfreeze_all",
        {},
        created_by=int(callback.from_user.id),
    )
    note = (
        "✅ Додано в чергу: <b>Розморозити всі сенсори</b>\n"
        f"Job: <code>#{job_id}</code>"
    )
    await _render_sensors_page(
        callback.bot,
        callback.message.chat.id,
        offset=0,
        prefer_message_id=callback.message.message_id,
        note=note,
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
        until_str = "до ручної розморозки" if _is_freeze_forever(frozen_until) else frozen_until.strftime("%d.%m %H:%M")
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
                InlineKeyboardButton(text="🧊 +6 год", callback_data=f"admin_sensor_freeze|{uuid}|21600"),
            ]
        )
        rows.append([InlineKeyboardButton(text="🧊 До розморозки", callback_data=f"admin_sensor_freeze|{uuid}|{SENSORS_FREEZE_FOREVER_TOKEN}")])
    else:
        rows.append(
            [
                InlineKeyboardButton(text="🧊 15 хв", callback_data=f"admin_sensor_freeze|{uuid}|900"),
                InlineKeyboardButton(text="🧊 1 год", callback_data=f"admin_sensor_freeze|{uuid}|3600"),
                InlineKeyboardButton(text="🧊 6 год", callback_data=f"admin_sensor_freeze|{uuid}|21600"),
            ]
        )
        rows.append([InlineKeyboardButton(text="🧊 До розморозки", callback_data=f"admin_sensor_freeze|{uuid}|{SENSORS_FREEZE_FOREVER_TOKEN}")])

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
            # Prefer stable order 1..N (building-specific); keep legacy NULL at the end.
            for sid in get_building_section_ids(int(bid)):
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


def _biz_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛡 Модерація", callback_data=CB_BIZ_MOD),
                InlineKeyboardButton(text="📝 Правки закладів", callback_data=CB_BIZ_REPORTS),
            ],
            [
                InlineKeyboardButton(text="🔐 Коди прив'язки", callback_data=CB_BIZ_TOK_MENU),
                InlineKeyboardButton(text="🏢 Заклади", callback_data=CB_BIZ_PLACES_MENU),
            ],
            [
                InlineKeyboardButton(text="💳 Підписки", callback_data=CB_BIZ_SUBS),
                InlineKeyboardButton(text="➕ Додати заклад", callback_data=CB_BIZ_CREATE_PLACE_MENU),
            ],
            [
                InlineKeyboardButton(text="🗂 Категорії", callback_data=CB_BIZ_CATEGORIES_MENU),
                InlineKeyboardButton(text="💸 Платежі", callback_data=CB_BIZ_PAYMENTS),
            ],
            [InlineKeyboardButton(text="📒 Аудит", callback_data=CB_BIZ_AUDIT)],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_refresh")],
        ]
    )


async def _render_business_menu(bot: Bot, chat_id: int, *, prefer_message_id: int | None, note: str | None = None) -> None:
    text = "🏢 <b>Бізнес</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть дію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=_biz_menu_keyboard(),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_MENU)
async def cb_business_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await _render_business_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)


def _subscription_status_title(raw: str | None) -> str:
    status = (raw or "").strip().lower()
    if status == "active":
        return "🟢 Активна"
    if status == "past_due":
        return "🟠 Прострочена"
    if status == "canceled":
        return "🔴 Скасована"
    if status == "inactive":
        return "⚪ Неактивна"
    return f"⚫ {status or 'невідомо'}"


def _subscription_tier_title(raw: str | None) -> str:
    tier = (raw or "").strip().lower()
    return PLAN_TITLES.get(tier, tier or PLAN_TITLES["free"])


def _owner_status_title(raw: str | None) -> str:
    status = (raw or "").strip().lower()
    if status == "approved":
        return "✅ Підтверджено"
    if status == "pending":
        return "🕓 Очікує модерації"
    if status == "rejected":
        return "❌ Відхилено"
    return f"⚫ {status or 'невідомо'}"


def _subscription_visibility_title(is_published: int | bool | None) -> str:
    return "✅ Опубліковано" if int(is_published or 0) == 1 else "📝 Чернетка"


def _subscription_verified_title(is_verified: int | bool | None) -> str:
    return "✅ Verified" if int(is_verified or 0) == 1 else "—"


def _report_priority_title(priority_score: int | None) -> str:
    score = int(priority_score or 0)
    if score >= 2:
        return "🔥 Premium/Partner"
    if score == 1:
        return "🟡 Light"
    return "⚪ Звичайний"


def _payment_event_title(raw: str | None) -> str:
    event = (raw or "").strip().lower()
    if event == "invoice_created":
        return "🧾 Інвойс створено"
    if event == "pre_checkout_ok":
        return "✅ Pre-checkout OK"
    if event == "payment_succeeded":
        return "💚 Оплата успішна"
    if event == "payment_failed":
        return "❌ Оплата помилкова"
    if event == "payment_canceled":
        return "🚫 Оплату скасовано"
    if event == "refund":
        return "↩️ Повернення"
    return f"⚪ {event or 'невідомо'}"


def _payment_status_title(raw: str | None) -> str:
    status = (raw or "").strip().lower()
    if status == "processed":
        return "✅ processed"
    if status == "new":
        return "🕓 new"
    if status == "failed":
        return "❌ failed"
    if status == "canceled":
        return "🚫 canceled"
    return f"⚪ {status or 'unknown'}"


def _short_external_id(raw: str | None, *, limit: int = 42) -> str:
    value = str(raw or "").strip()
    if not value:
        return "—"
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _format_tg_contact(*, tg_user_id: int | None, username: str | None, first_name: str | None) -> str:
    uid = int(tg_user_id or 0)
    uname = str(username or "").strip()
    fname = str(first_name or "").strip()
    label = f"@{uname}" if uname else (fname or "невідомо")
    safe_label = escape(label)
    if uid > 0:
        return f'<a href="tg://user?id={uid}">{safe_label}</a> / <code>{uid}</code>'
    return safe_label


async def _render_business_subscriptions(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    prefer_message_id: int | None,
) -> None:
    admin_id = int(chat_id)
    offset = max(0, int(page)) * BIZ_SUBS_PAGE_SIZE
    try:
        rows, total = await business_service.list_all_subscriptions_admin(
            admin_id,
            limit=BIZ_SUBS_PAGE_SIZE,
            offset=offset,
        )
    except BusinessAccessDeniedError as error:
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note=f"❌ {escape(str(error))}")
        return
    except Exception:
        logger.exception("Failed to load business subscriptions")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження підписок.")
        return

    if total <= 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
            ]
        )
        await render(
            bot,
            chat_id=chat_id,
            text="💳 <b>Підписки</b>\n\nНаразі немає даних.",
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    total_pages = max(1, (int(total) + BIZ_SUBS_PAGE_SIZE - 1) // BIZ_SUBS_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    # Re-fetch if page was clamped.
    if safe_page != int(page):
        rows, _ = await business_service.list_all_subscriptions_admin(
            admin_id,
            limit=BIZ_SUBS_PAGE_SIZE,
            offset=safe_page * BIZ_SUBS_PAGE_SIZE,
        )

    lines = ["💳 <b>Підписки бізнесів</b>", "", f"Записів: <b>{int(total)}</b>", ""]
    for row in rows:
        place_id = int(row.get("place_id") or 0)
        place_name = escape(str(row.get("place_name") or f"ID {place_id}"))
        tier = escape(_subscription_tier_title(str(row.get("tier") or "free")))
        status = _subscription_status_title(str(row.get("status") or "inactive"))
        expires_at = escape(str(row.get("expires_at") or "—"))
        visibility = _subscription_visibility_title(row.get("is_published"))
        verified = _subscription_verified_title(row.get("is_verified"))
        owner_contact = _format_tg_contact(
            tg_user_id=row.get("owner_tg_user_id"),
            username=row.get("owner_username"),
            first_name=row.get("owner_first_name"),
        )
        owner_status = _owner_status_title(str(row.get("owner_status") or ""))
        lines.append(
            f"• <b>{place_name}</b> <code>#{place_id}</code>\n"
            f"  Тариф: <code>{tier}</code> | Стан: {status}\n"
            f"  Діє до: <code>{expires_at}</code>\n"
            f"  Власник: {owner_contact} ({owner_status})\n"
            f"  {visibility} | {verified}"
        )
        lines.append("")

    kb_rows: list[list[InlineKeyboardButton]] = []
    # Admin fallback: allow marking successful payments as refunded (revokes entitlements).
    for row in rows:
        try:
            event_id = int(row.get("id") or 0)
        except Exception:
            continue
        if event_id <= 0:
            continue
        event_type = str(row.get("event_type") or "").strip().lower()
        provider = str(row.get("provider") or "").strip().lower()
        if event_type == "payment_succeeded" and provider == "telegram_stars":
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"↩️ Refund #{event_id}",
                        callback_data=f"{CB_BIZ_PAY_REFUND_PREFIX}{event_id}|{safe_page}",
                    )
                ]
            )
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_SUBS_PAGE_PREFIX}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_SUBS_PAGE_PREFIX}{safe_page + 1}",
                )
            )
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="📄 Експорт (файл)", callback_data=CB_BIZ_SUBS_EXPORT)])
    kb_rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    kb_rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])
    await render(
        bot,
        chat_id=chat_id,
        text="\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_SUBS_EXPORT)
async def cb_business_subscriptions_export(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Формую файл…")
    admin_id = int(callback.from_user.id)

    try:
        all_rows: list[dict] = []
        page_size = 50
        offset = 0
        while True:
            rows, total = await business_service.list_all_subscriptions_admin(
                admin_id,
                limit=page_size,
                offset=offset,
            )
            all_rows.extend(rows)
            offset += len(rows)
            if not rows or offset >= int(total):
                break
    except BusinessAccessDeniedError as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to export business subscriptions")
        await callback.answer("❌ Не вдалося сформувати експорт", show_alert=True)
        return

    lines: list[str] = [
        "place_id\tplace_name\ttier\tstatus\texpires_at\towner_tg_user_id\towner_username\towner_first_name\towner_status\towner_created_at\tis_published\tis_verified\tbusiness_enabled\tverified_tier\tverified_until\tupdated_at"
    ]
    for row in all_rows:
        place_id = int(row.get("place_id") or 0)
        place_name = str(row.get("place_name") or "").replace("\t", " ").replace("\n", " ").strip()
        tier = str(row.get("tier") or "free")
        status = str(row.get("status") or "inactive")
        expires_at = str(row.get("expires_at") or "")
        owner_tg_user_id = int(row.get("owner_tg_user_id") or 0)
        owner_username = str(row.get("owner_username") or "").replace("\t", " ").replace("\n", " ").strip()
        owner_first_name = str(row.get("owner_first_name") or "").replace("\t", " ").replace("\n", " ").strip()
        owner_status = str(row.get("owner_status") or "")
        owner_created_at = str(row.get("owner_created_at") or "")
        is_published = int(row.get("is_published") or 0)
        is_verified = int(row.get("is_verified") or 0)
        business_enabled = int(row.get("business_enabled") or 0)
        verified_tier = str(row.get("verified_tier") or "")
        verified_until = str(row.get("verified_until") or "")
        updated_at = str(row.get("updated_at") or "")
        lines.append(
            f"{place_id}\t{place_name}\t{tier}\t{status}\t{expires_at}\t"
            f"{owner_tg_user_id}\t{owner_username}\t{owner_first_name}\t{owner_status}\t{owner_created_at}\t"
            f"{is_published}\t{is_verified}\t{business_enabled}\t{verified_tier}\t{verified_until}\t{updated_at}"
        )

    payload = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(payload, filename="business_subscriptions.tsv")
    caption = f"💳 Експорт підписок: {len(all_rows)} записів."
    target_message = callback.message
    try:
        if target_message:
            await target_message.answer_document(document=file, caption=caption)
        else:
            await callback.bot.send_document(chat_id=int(callback.from_user.id), document=file, caption=caption)
    except Exception:
        logger.exception("Failed to send business subscriptions export file")
        await callback.answer("❌ Не вдалося надіслати файл", show_alert=True)


@router.callback_query(F.data == CB_BIZ_SUBS)
async def cb_business_subscriptions(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_business_subscriptions(
        callback.bot,
        callback.message.chat.id,
        page=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_SUBS_PAGE_PREFIX))
async def cb_business_subscriptions_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_business_subscriptions(
        callback.bot,
        callback.message.chat.id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


async def _render_business_payments(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    admin_id = int(chat_id)
    offset = max(0, int(page)) * BIZ_PAYMENTS_PAGE_SIZE
    try:
        rows, total = await business_service.list_payment_events_admin(
            admin_id,
            limit=BIZ_PAYMENTS_PAGE_SIZE,
            offset=offset,
        )
    except BusinessAccessDeniedError as error:
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note=f"❌ {escape(str(error))}")
        return
    except Exception:
        logger.exception("Failed to load business payment events")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження платежів.")
        return

    if total <= 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
            ]
        )
        await render(
            bot,
            chat_id=chat_id,
            text="💸 <b>Платежі</b>\n\nНаразі немає подій.",
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    total_pages = max(1, (int(total) + BIZ_PAYMENTS_PAGE_SIZE - 1) // BIZ_PAYMENTS_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    if safe_page != int(page):
        rows, _ = await business_service.list_payment_events_admin(
            admin_id,
            limit=BIZ_PAYMENTS_PAGE_SIZE,
            offset=safe_page * BIZ_PAYMENTS_PAGE_SIZE,
        )

    lines = ["💸 <b>Платіжні події</b>"]
    if note:
        lines.extend(["", escape(str(note).strip())])
    lines.extend(["", f"Подій: <b>{int(total)}</b>", ""])
    for row in rows:
        event_id = int(row.get("id") or 0)
        place_id = int(row.get("place_id") or 0)
        place_name = escape(str(row.get("place_name") or f"ID {place_id}"))
        provider = escape(str(row.get("provider") or "—"))
        event_title = _payment_event_title(str(row.get("event_type") or ""))
        status_title = _payment_status_title(str(row.get("status") or ""))
        amount = row.get("amount_stars")
        amount_text = f"{int(amount)}⭐" if amount is not None else "—"
        currency = escape(str(row.get("currency") or "XTR"))
        created_at = escape(str(row.get("created_at") or "—"))
        external_payment_id = escape(_short_external_id(row.get("external_payment_id")))
        owner_contact = _format_tg_contact(
            tg_user_id=row.get("owner_tg_user_id"),
            username=row.get("owner_username"),
            first_name=row.get("owner_first_name"),
        )
        owner_status = _owner_status_title(str(row.get("owner_status") or ""))
        lines.append(
            f"• <code>#{event_id}</code> {event_title}\n"
            f"  Заклад: <b>{place_name}</b> <code>#{place_id}</code>\n"
            f"  Сума: <code>{amount_text}</code> {currency} | {status_title}\n"
            f"  Провайдер: <code>{provider}</code>\n"
            f"  ext: <code>{external_payment_id}</code>\n"
            f"  Власник: {owner_contact} ({owner_status})\n"
            f"  <code>{created_at}</code>"
        )
        lines.append("")

    kb_rows: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_PAYMENTS_PAGE_PREFIX}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_PAYMENTS_PAGE_PREFIX}{safe_page + 1}",
                )
            )
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="📄 Експорт (файл)", callback_data=CB_BIZ_PAYMENTS_EXPORT)])
    kb_rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    kb_rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])
    await render(
        bot,
        chat_id=chat_id,
        text="\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_PAYMENTS_EXPORT)
async def cb_business_payments_export(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Формую файл…")
    admin_id = int(callback.from_user.id)

    try:
        all_rows: list[dict] = []
        page_size = 100
        offset = 0
        while True:
            rows, total = await business_service.list_payment_events_admin(
                admin_id,
                limit=page_size,
                offset=offset,
            )
            all_rows.extend(rows)
            offset += len(rows)
            if not rows or offset >= int(total):
                break
    except BusinessAccessDeniedError as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to export business payment events")
        await callback.answer("❌ Не вдалося сформувати експорт", show_alert=True)
        return

    lines: list[str] = [
        "id\tplace_id\tplace_name\tprovider\texternal_payment_id\tevent_type\tamount_stars\tcurrency\tstatus\towner_tg_user_id\towner_username\towner_first_name\towner_status\tcreated_at\tprocessed_at"
    ]
    for row in all_rows:
        event_id = int(row.get("id") or 0)
        place_id = int(row.get("place_id") or 0)
        place_name = str(row.get("place_name") or "").replace("\t", " ").replace("\n", " ").strip()
        provider = str(row.get("provider") or "")
        external_payment_id = str(row.get("external_payment_id") or "").replace("\t", " ").replace("\n", " ").strip()
        event_type = str(row.get("event_type") or "")
        amount_stars = row.get("amount_stars")
        amount_stars_text = "" if amount_stars is None else str(int(amount_stars))
        currency = str(row.get("currency") or "")
        status = str(row.get("status") or "")
        owner_tg_user_id = int(row.get("owner_tg_user_id") or 0)
        owner_username = str(row.get("owner_username") or "").replace("\t", " ").replace("\n", " ").strip()
        owner_first_name = str(row.get("owner_first_name") or "").replace("\t", " ").replace("\n", " ").strip()
        owner_status = str(row.get("owner_status") or "")
        created_at = str(row.get("created_at") or "")
        processed_at = str(row.get("processed_at") or "")
        lines.append(
            f"{event_id}\t{place_id}\t{place_name}\t{provider}\t{external_payment_id}\t{event_type}\t"
            f"{amount_stars_text}\t{currency}\t{status}\t{owner_tg_user_id}\t{owner_username}\t"
            f"{owner_first_name}\t{owner_status}\t{created_at}\t{processed_at}"
        )

    payload = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(payload, filename="business_payments.tsv")
    caption = f"💸 Експорт платежів: {len(all_rows)} подій."
    target_message = callback.message
    try:
        if target_message:
            await target_message.answer_document(document=file, caption=caption)
        else:
            await callback.bot.send_document(chat_id=int(callback.from_user.id), document=file, caption=caption)
    except Exception:
        logger.exception("Failed to send business payment events export file")
        await callback.answer("❌ Не вдалося надіслати файл", show_alert=True)


@router.callback_query(F.data == CB_BIZ_PAYMENTS)
async def cb_business_payments(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_business_payments(
        callback.bot,
        callback.message.chat.id,
        page=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PAYMENTS_PAGE_PREFIX))
async def cb_business_payments_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_business_payments(
        callback.bot,
        callback.message.chat.id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


async def _render_business_payment_refund_confirm(
    bot: Bot,
    chat_id: int,
    *,
    event_id: int,
    page: int,
    prefer_message_id: int | None,
) -> None:
    admin_id = int(chat_id)
    try:
        row = await business_service.get_payment_event_admin(admin_id, event_id=int(event_id))
    except BusinessAccessDeniedError as error:
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note=f"❌ {escape(str(error))}")
        return
    except BusinessNotFoundError as error:
        await _render_business_payments(
            bot,
            chat_id,
            page=int(page),
            prefer_message_id=prefer_message_id,
            note=f"❌ {escape(str(error))}",
        )
        return
    except Exception:
        logger.exception("Failed to load payment event for refund confirm")
        await _render_business_payments(
            bot,
            chat_id,
            page=int(page),
            prefer_message_id=prefer_message_id,
            note="❌ Не вдалося завантажити платіжну подію.",
        )
        return

    provider = escape(str(row.get("provider") or "—"))
    event_title = _payment_event_title(str(row.get("event_type") or ""))
    status_title = _payment_status_title(str(row.get("status") or ""))
    place_id = int(row.get("place_id") or 0)
    place_name = escape(str(row.get("place_name") or f"ID {place_id}"))
    amount = row.get("amount_stars")
    amount_text = f"{int(amount)}⭐" if amount is not None else "—"
    currency = escape(str(row.get("currency") or "XTR"))
    external_payment_id = escape(_short_external_id(row.get("external_payment_id")))
    created_at = escape(str(row.get("created_at") or "—"))

    text = (
        "↩️ <b>Повернення платежу (refund)</b>\n\n"
        f"Подія: <code>#{int(event_id)}</code> {event_title}\n"
        f"Заклад: <b>{place_name}</b> <code>#{place_id}</code>\n"
        f"Сума: <code>{amount_text}</code> {currency} | {status_title}\n"
        f"Провайдер: <code>{provider}</code>\n"
        f"ext: <code>{external_payment_id}</code>\n"
        f"<code>{created_at}</code>\n\n"
        "Ця дія одразу переведе підписку в <b>Free</b> і вимкне <b>Verified</b>.\n"
        "Підтвердити?"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Підтвердити refund",
                    callback_data=f"{CB_BIZ_PAY_REFUND_CONFIRM_PREFIX}{int(event_id)}|{int(page)}",
                )
            ],
            [InlineKeyboardButton(text="« Назад до платежів", callback_data=f"{CB_BIZ_PAYMENTS_PAGE_PREFIX}{int(page)}")],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
            [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
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


@router.callback_query(F.data.startswith(CB_BIZ_PAY_REFUND_PREFIX))
async def cb_business_payment_refund(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        raw = callback.data.split("|", 2)
        event_id = int(raw[1]) if len(raw) > 1 else 0
        page = int(raw[2]) if len(raw) > 2 else 0
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await _render_business_payment_refund_confirm(
        callback.bot,
        callback.message.chat.id,
        event_id=event_id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PAY_REFUND_CONFIRM_PREFIX))
async def cb_business_payment_refund_confirm(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Обробляю…")
    admin_id = int(callback.from_user.id)
    try:
        raw = callback.data.split("|", 2)
        event_id = int(raw[1]) if len(raw) > 1 else 0
        page = int(raw[2]) if len(raw) > 2 else 0
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    if event_id <= 0:
        await callback.answer("Некоректна подія", show_alert=True)
        return

    try:
        outcome = await business_service.admin_mark_payment_refund(admin_id, event_id=event_id)
    except BusinessAccessDeniedError as error:
        await callback.answer(str(error), show_alert=True)
        return
    except (BusinessValidationError, BusinessNotFoundError) as error:
        await _render_business_payment_refund_confirm(
            callback.bot,
            callback.message.chat.id,
            event_id=event_id,
            page=page,
            prefer_message_id=callback.message.message_id,
        )
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to mark payment refund event_id=%s", event_id)
        await callback.answer("❌ Помилка обробки refund", show_alert=True)
        return

    note = "ℹ️ Refund вже був оброблений раніше." if outcome.get("duplicate") else "✅ Refund зафіксовано. Verified вимкнено."
    await _render_business_payments(
        callback.bot,
        callback.message.chat.id,
        page=int(page),
        prefer_message_id=callback.message.message_id,
        note=note,
    )


def _audit_action_short(action: str | None) -> str:
    raw = (action or "").strip()
    return raw if raw else "unknown"


async def _render_business_audit(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    prefer_message_id: int | None,
) -> None:
    admin_id = int(chat_id)
    offset = max(0, int(page)) * BIZ_AUDIT_PAGE_SIZE
    try:
        rows, total = await business_service.list_audit_logs_admin(
            admin_id,
            limit=BIZ_AUDIT_PAGE_SIZE,
            offset=offset,
            place_id=None,
        )
    except BusinessAccessDeniedError as error:
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note=f"❌ {escape(str(error))}")
        return
    except Exception:
        logger.exception("Failed to load business audit logs")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження аудиту.")
        return

    if total <= 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
            ]
        )
        await render(
            bot,
            chat_id=chat_id,
            text="📒 <b>Аудит</b>\n\nЛог порожній.",
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    total_pages = max(1, (int(total) + BIZ_AUDIT_PAGE_SIZE - 1) // BIZ_AUDIT_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    if safe_page != int(page):
        rows, _ = await business_service.list_audit_logs_admin(
            admin_id,
            limit=BIZ_AUDIT_PAGE_SIZE,
            offset=safe_page * BIZ_AUDIT_PAGE_SIZE,
            place_id=None,
        )

    lines = [
        "📒 <b>Аудит</b>",
        "",
        f"Записів: <b>{int(total)}</b>",
        "",
    ]
    for row in rows:
        aid = int(row.get("id") or 0)
        place_id = int(row.get("place_id") or 0)
        place_name = escape(str(row.get("place_name") or f"ID {place_id}"))
        actor = row.get("actor_tg_user_id")
        actor_txt = f"<code>{int(actor)}</code>" if actor is not None else "system"
        action = escape(_audit_action_short(str(row.get("action") or "")))
        created_at = escape(str(row.get("created_at") or "—"))
        payload = str(row.get("payload_json") or "")
        payload_short = escape(payload[:160] + ("…" if len(payload) > 160 else ""))
        lines.append(
            f"• <code>#{aid}</code> {action}\n"
            f"  place=<code>{place_id}</code> <b>{place_name}</b>\n"
            f"  by={actor_txt} at <code>{created_at}</code>\n"
            f"  payload: <code>{payload_short or '—'}</code>"
        )
        lines.append("")

    kb_rows: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_AUDIT_PAGE_PREFIX}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_AUDIT_PAGE_PREFIX}{safe_page + 1}",
                )
            )
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    kb_rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])
    await render(
        bot,
        chat_id=chat_id,
        text="\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_AUDIT)
async def cb_business_audit(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_business_audit(
        callback.bot,
        callback.message.chat.id,
        page=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_AUDIT_PAGE_PREFIX))
async def cb_business_audit_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_business_audit(
        callback.bot,
        callback.message.chat.id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


def _biz_moderation_keyboard(owner_id: int, *, index: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            ikb(
                text="✅ Підтвердити",
                callback_data=f"{CB_BIZ_MOD_APPROVE_PREFIX}{int(owner_id)}|{int(index)}",
                style=STYLE_SUCCESS,
            ),
            ikb(
                text="❌ Відхилити",
                callback_data=f"{CB_BIZ_MOD_REJECT_PREFIX}{int(owner_id)}|{int(index)}",
                style=STYLE_DANGER,
            ),
        ]
    ]
    if total > 1:
        nav: list[InlineKeyboardButton] = []
        if index > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_MOD_PAGE_PREFIX}{index - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=CB_ADMIN_NOOP))
        if index < total - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_MOD_PAGE_PREFIX}{index + 1}",
                )
            )
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_business_moderation(
    bot: Bot,
    chat_id: int,
    *,
    index: int,
    owner_id: int | None = None,
    prefer_message_id: int | None = None,
) -> None:
    admin_id = int(chat_id)
    try:
        rows = await business_service.list_pending_owner_requests(admin_id)
    except BusinessAccessDeniedError as error:
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note=f"❌ {escape(str(error))}")
        return
    except Exception:
        logger.exception("Failed to load business moderation queue")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження модерації.")
        return

    if not rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
            ]
        )
        await render(
            bot,
            chat_id=chat_id,
            text="🛡 <b>Модерація</b>\n\nЧерга модерації порожня.",
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    notice = ""
    if owner_id:
        found_index = next(
            (idx for idx, row in enumerate(rows) if int(row.get("owner_id") or 0) == int(owner_id)),
            None,
        )
        if found_index is not None:
            safe_index = int(found_index)
        else:
            safe_index = max(0, min(int(index), len(rows) - 1))
            notice = f"⚠️ Заявку <code>{owner_id}</code> не знайдено в pending-черзі."
    else:
        safe_index = max(0, min(int(index), len(rows) - 1))
    item = rows[safe_index]

    user_contact = _format_tg_contact(
        tg_user_id=item.get("tg_user_id"),
        username=item.get("username"),
        first_name=item.get("first_name"),
    )
    place_name = escape(str(item.get("place_name") or "—"))
    place_address = escape(str(item.get("place_address") or "—"))

    text = "🛡 <b>Модерація</b>\n\n"
    if notice:
        text += f"{notice}\n\n"
    text += (
        f"Заявка: <code>{item['owner_id']}</code>\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{item['place_id']}</code>)\n"
        f"Адреса: {place_address}\n"
        f"Власник: {user_contact}\n"
        f"Створено: {escape(str(item.get('created_at') or ''))}"
    )
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=_biz_moderation_keyboard(int(item["owner_id"]), index=safe_index, total=len(rows)),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


def _biz_reports_keyboard(item: dict, *, index: int, total: int) -> InlineKeyboardMarkup:
    report_id = int(item.get("id") or 0)
    place_id = int(item.get("place_id") or 0)
    service_id = int(item.get("service_id") or 0)
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✅ Позначити опрацьованим", callback_data=f"{CB_BIZ_REPORTS_RESOLVE_PREFIX}{report_id}|{index}")],
    ]
    if place_id > 0 and service_id > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🏢 Відкрити заклад",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}all|{place_id}|{service_id}|0|0",
                )
            ]
        )
    if total > 1:
        nav: list[InlineKeyboardButton] = []
        if index > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_BIZ_REPORTS_PAGE_PREFIX}{index - 1}"))
        nav.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=CB_ADMIN_NOOP))
        if index < total - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_BIZ_REPORTS_PAGE_PREFIX}{index + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_business_reports(
    bot: Bot,
    chat_id: int,
    *,
    index: int,
    report_id: int | None = None,
    prefer_message_id: int | None = None,
) -> None:
    try:
        rows, total = await list_place_reports(status="pending", limit=5000, offset=0)
    except Exception:
        logger.exception("Failed to load place reports moderation queue")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження правок.")
        return
    if total <= 0 or not rows:
        await render(
            bot,
            chat_id=chat_id,
            text="📝 <b>Правки закладів</b>\n\nЧерга порожня.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                    [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
                ]
            ),
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    visible_total = len(rows)
    safe_index = max(0, min(int(index), visible_total - 1))
    notice = ""
    if report_id is not None and int(report_id) > 0:
        found = next((i for i, row in enumerate(rows) if int(row.get("id") or 0) == int(report_id)), None)
        if found is not None:
            safe_index = int(found)
        else:
            notice = f"⚠️ Репорт <code>{int(report_id)}</code> не знайдено в pending-черзі.\n\n"

    item = rows[safe_index]
    reporter_contact = _format_tg_contact(
        tg_user_id=item.get("reporter_tg_user_id"),
        username=item.get("reporter_username"),
        first_name=item.get("reporter_first_name"),
    )
    place_name = escape(str(item.get("place_name") or f"ID {int(item.get('place_id') or 0)}"))
    place_address = escape(str(item.get("place_address") or "—"))
    service_name = escape(str(item.get("service_name") or "—"))
    report_text = escape(str(item.get("report_text") or "—"))
    priority_title = _report_priority_title(item.get("priority_score"))
    text = (
        "📝 <b>Правки закладів</b>\n\n"
        f"{notice}"
        f"Репорт: <code>{int(item.get('id') or 0)}</code>\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{int(item.get('place_id') or 0)}</code>)\n"
        f"Категорія: {service_name}\n"
        f"Пріоритет: {priority_title}\n"
        f"Адреса: {place_address}\n"
        f"Від: {reporter_contact}\n"
        f"Створено: {escape(str(item.get('created_at') or ''))}\n\n"
        f"Текст:\n{report_text}"
    )
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=_biz_reports_keyboard(item, index=safe_index, total=visible_total),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_MOD)
async def cb_business_moderation(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_business_moderation(
        callback.bot,
        callback.message.chat.id,
        index=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_MOD_PAGE_PREFIX))
async def cb_business_moderation_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        index = int(callback.data.split("|", 1)[1])
    except Exception:
        index = 0
    await _render_business_moderation(
        callback.bot,
        callback.message.chat.id,
        index=index,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_MOD_JUMP_PREFIX))
async def cb_business_moderation_jump(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        owner_id = int(callback.data.split("|", 1)[1])
    except Exception:
        owner_id = 0
    await _render_business_moderation(
        callback.bot,
        callback.message.chat.id,
        index=0,
        owner_id=owner_id if owner_id > 0 else None,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == CB_BIZ_REPORTS)
async def cb_business_reports(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_business_reports(
        callback.bot,
        callback.message.chat.id,
        index=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_REPORTS_PAGE_PREFIX))
async def cb_business_reports_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        index = int(callback.data.split("|", 1)[1])
    except Exception:
        index = 0
    await _render_business_reports(
        callback.bot,
        callback.message.chat.id,
        index=index,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_REPORTS_JUMP_PREFIX))
async def cb_business_reports_jump(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        report_id = int(callback.data.split("|", 1)[1])
    except Exception:
        report_id = 0
    await _render_business_reports(
        callback.bot,
        callback.message.chat.id,
        index=0,
        report_id=report_id if report_id > 0 else None,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_REPORTS_RESOLVE_PREFIX))
async def cb_business_reports_resolve(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    raw = callback.data[len(CB_BIZ_REPORTS_RESOLVE_PREFIX) :]
    parts = raw.split("|", 1)
    if len(parts) != 2:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        report_id = int(parts[0])
        index = int(parts[1])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        updated = await set_place_report_status(report_id, "resolved", resolved_by=int(callback.from_user.id))
    except Exception:
        logger.exception("Failed to resolve place report report_id=%s", report_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return
    if not updated:
        await callback.answer("⚠️ Репорт уже опрацьовано або не знайдено", show_alert=True)
    else:
        await callback.answer("✅ Позначено як опрацьований")
    await _render_business_reports(
        callback.bot,
        callback.message.chat.id,
        index=index,
        prefer_message_id=callback.message.message_id,
    )


async def _notify_owner_via_business_bot(owner_tg_user_id: int, text: str) -> None:
    token = (CFG.business_bot_api_key or "").strip()
    if not token:
        return
    bot = Bot(token=token)
    try:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏢 Відкрити бізнес‑кабінет", callback_data="bmenu:home")]]
        )
        # Keep owner's chat clean: business-bot is also single-message by default,
        # so we render this notification as the current UI message (best-effort).
        await render_business_ui(
            bot,
            chat_id=int(owner_tg_user_id),
            text=str(text),
            reply_markup=kb,
            force_new_message=True,
        )
    except Exception:
        # Owner may block the bot or have not started it.
        logger.exception("Failed to notify business owner %s", owner_tg_user_id)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


@router.callback_query(F.data.startswith(CB_BIZ_MOD_APPROVE_PREFIX))
async def cb_business_moderation_approve(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    raw = callback.data.split("|")
    if len(raw) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        owner_id = int(raw[1])
        index = int(raw[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        updated = await business_service.approve_owner_request(int(callback.from_user.id), owner_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to approve business owner request")
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await _notify_owner_via_business_bot(
        int(updated["tg_user_id"]),
        "✅ Твою заявку на керування бізнесом підтверджено.\nТепер доступні редагування і керування тарифом.",
    )

    await callback.answer("✅ Approved")
    await _render_business_moderation(
        callback.bot,
        callback.message.chat.id,
        index=index,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_MOD_REJECT_PREFIX))
async def cb_business_moderation_reject(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    raw = callback.data.split("|")
    if len(raw) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        owner_id = int(raw[1])
        index = int(raw[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        updated = await business_service.reject_owner_request(int(callback.from_user.id), owner_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to reject business owner request")
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await _notify_owner_via_business_bot(
        int(updated["tg_user_id"]),
        "❌ Твою заявку на керування бізнесом відхилено адміністратором.",
    )

    await callback.answer("✅ Rejected")
    await _render_business_moderation(
        callback.bot,
        callback.message.chat.id,
        index=index,
        prefer_message_id=callback.message.message_id,
    )


def _biz_tokens_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Список закладів", callback_data=CB_BIZ_TOK_LIST)],
            [InlineKeyboardButton(text="♻️ Згенерувати коди", callback_data=CB_BIZ_TOK_GEN)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
            [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
        ]
    )


async def _render_biz_tokens_menu(bot: Bot, chat_id: int, *, prefer_message_id: int | None, note: str | None = None) -> None:
    text = "🔐 <b>Коди прив'язки</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть дію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=_biz_tokens_menu_keyboard(),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_TOK_MENU)
async def cb_biz_tok_menu(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_biz_tokens_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)


@router.callback_query(F.data == CB_BIZ_TOK_LIST)
async def cb_biz_tok_list(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_biz_token_services(
        callback.bot,
        callback.message.chat.id,
        page=0,
        mode="view",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == CB_BIZ_TOK_GEN)
async def cb_biz_tok_gen(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Для всіх закладів", callback_data=CB_BIZ_TOK_GEN_ALL)],
            [InlineKeyboardButton(text="🏢 Для закладу", callback_data=f"{CB_BIZ_TOKG_SVC_PAGE_PREFIX}0")],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_BIZ_TOK_MENU)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
        ]
    )
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text="♻️ <b>Згенерувати коди</b>\n\nОберіть дію:",
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_TOK_GEN_ALL)
async def cb_biz_tok_gen_all(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Так, згенерувати", callback_data=CB_BIZ_TOK_GEN_ALL_CONFIRM)],
            [InlineKeyboardButton(text="« Назад", callback_data=CB_BIZ_TOK_GEN)],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_BIZ_TOK_MENU)],
        ]
    )
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text="⚠️ <b>Увага</b>\n\nЦе згенерує нові коди для <b>всіх</b> закладів.\nПродовжити?",
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_TOK_GEN_ALL_CONFIRM)
async def cb_biz_tok_gen_all_confirm(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Генерую…")
    try:
        result = await business_service.bulk_rotate_claim_tokens_for_all_places(int(callback.from_user.id))
    except Exception:
        logger.exception("Failed to bulk rotate claim tokens")
        await callback.answer("❌ Помилка", show_alert=True)
        await _render_biz_tokens_menu(
            callback.bot,
            callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            note="❌ Не вдалося згенерувати коди.",
        )
        return

    total_places = int(result.get("total_places") or 0)
    rotated = int(result.get("rotated") or 0)
    note = f"✅ Готово.\nЗгенеровано: <b>{rotated}</b> з <b>{total_places}</b>."
    await _render_biz_tokens_menu(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id, note=note)


def _truncate_label(value: str, limit: int = 34) -> str:
    clean = (value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _format_service_button(service: dict) -> str:
    name = str(service.get("name") or "").strip() or f"ID {service.get('id')}"
    count = service.get("place_count")
    try:
        count_int = int(count)
    except Exception:
        count_int = None
    label = f"{name} ({count_int})" if count_int is not None else name
    return _truncate_label(label, 30)


async def _render_biz_token_services(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    mode: str,
    prefer_message_id: int | None,
) -> None:
    try:
        services = await business_repo.list_services_with_place_counts()
    except Exception:
        logger.exception("Failed to list services for claim tokens")
        await _render_biz_tokens_menu(
            bot,
            chat_id,
            prefer_message_id=prefer_message_id,
            note="❌ Не вдалося завантажити категорії.",
        )
        return

    if not services:
        await _render_biz_tokens_menu(
            bot,
            chat_id,
            prefer_message_id=prefer_message_id,
            note="Немає жодної категорії/закладів.",
        )
        return

    total_pages = max(1, (len(services) + BIZ_SERVICES_PAGE_SIZE - 1) // BIZ_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * BIZ_SERVICES_PAGE_SIZE
    chunk = services[start : start + BIZ_SERVICES_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in chunk:
        label = _format_service_button(svc)
        cb = (
            f"{CB_BIZ_TOKV_SVC_PICK_PREFIX}{int(svc['id'])}|{safe_page}"
            if mode == "view"
            else f"{CB_BIZ_TOKG_SVC_PICK_PREFIX}{int(svc['id'])}|{safe_page}"
        )
        buffer.append(InlineKeyboardButton(text=label, callback_data=cb))
        if len(buffer) >= 2:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{(CB_BIZ_TOKV_SVC_PAGE_PREFIX if mode == 'view' else CB_BIZ_TOKG_SVC_PAGE_PREFIX)}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{(CB_BIZ_TOKV_SVC_PAGE_PREFIX if mode == 'view' else CB_BIZ_TOKG_SVC_PAGE_PREFIX)}{safe_page + 1}",
                )
            )
        rows.append(nav)

    back_cb = CB_BIZ_TOK_MENU if mode == "view" else CB_BIZ_TOK_GEN
    back_label = "« Коди прив'язки" if mode == "view" else "« Згенерувати коди"
    rows.append([InlineKeyboardButton(text=back_label, callback_data=back_cb)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    title = "📋 <b>Список закладів</b>" if mode == "view" else "🏢 <b>Згенерувати код для закладу</b>"
    text = f"{title}\n\nОберіть категорію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKV_SVC_PAGE_PREFIX))
async def cb_biz_tokv_service_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_biz_token_services(
        callback.bot,
        callback.message.chat.id,
        page=page,
        mode="view",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKV_SVC_PICK_PREFIX))
async def cb_biz_tokv_service_pick(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректна категорія", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        service_page = int(parts[2])
    except Exception:
        service_id = 0
        service_page = 0
    await _render_biz_token_places(
        callback.bot,
        callback.message.chat.id,
        service_id=service_id,
        place_page=0,
        service_page=service_page,
        mode="view",
        prefer_message_id=callback.message.message_id,
    )


async def _render_biz_token_places(
    bot: Bot,
    chat_id: int,
    *,
    service_id: int,
    place_page: int,
    service_page: int,
    mode: str,
    prefer_message_id: int | None,
) -> None:
    try:
        total = await business_repo.count_places_by_service(int(service_id))
        total_pages = max(1, (total + BIZ_PLACES_PAGE_SIZE - 1) // BIZ_PLACES_PAGE_SIZE)
        safe_page = max(0, min(int(place_page), total_pages - 1))
        offset = safe_page * BIZ_PLACES_PAGE_SIZE
        places = await business_repo.list_places_by_service(
            int(service_id),
            limit=BIZ_PLACES_PAGE_SIZE,
            offset=offset,
        )
    except Exception:
        logger.exception("Failed to list places for service %s", service_id)
        await _render_biz_token_services(
            bot,
            chat_id,
            page=service_page,
            mode=mode,
            prefer_message_id=prefer_message_id,
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for p in places:
        pid = int(p.get("id") or 0)
        label = _truncate_label(str(p.get("name") or f"ID {pid}"), 38)
        if mode == "view":
            cb = f"{CB_BIZ_TOKV_PLACE_OPEN_PREFIX}{pid}|{int(service_id)}|{safe_page}|{int(service_page)}"
        else:
            cb = f"{CB_BIZ_TOKG_PLACE_ROTATE_PREFIX}{pid}|{int(service_id)}|{safe_page}|{int(service_page)}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{(CB_BIZ_TOKV_PLACE_PAGE_PREFIX if mode == 'view' else CB_BIZ_TOKG_PLACE_PAGE_PREFIX)}{int(service_id)}|{safe_page - 1}|{int(service_page)}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{(CB_BIZ_TOKV_PLACE_PAGE_PREFIX if mode == 'view' else CB_BIZ_TOKG_PLACE_PAGE_PREFIX)}{int(service_id)}|{safe_page + 1}|{int(service_page)}",
                )
            )
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="« Категорії",
                callback_data=f"{(CB_BIZ_TOKV_SVC_PAGE_PREFIX if mode == 'view' else CB_BIZ_TOKG_SVC_PAGE_PREFIX)}{int(service_page)}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    title = "📋 <b>Список закладів</b>" if mode == "view" else "🏢 <b>Згенерувати код</b>"
    text = f"{title}\n\nОберіть заклад:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKV_PLACE_PAGE_PREFIX))
async def cb_biz_tokv_place_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 4:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        place_page = int(parts[2])
        service_page = int(parts[3])
    except Exception:
        service_id = 0
        place_page = 0
        service_page = 0
    await _render_biz_token_places(
        callback.bot,
        callback.message.chat.id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        mode="view",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKV_PLACE_OPEN_PREFIX))
async def cb_biz_tokv_place_open(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳")
    parts = callback.data.split("|")
    if len(parts) < 5:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[1])
        service_id = int(parts[2])
        place_page = int(parts[3])
        service_page = int(parts[4])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        result = await business_service.get_or_create_active_claim_token_for_place(int(callback.from_user.id), place_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to get claim token for place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    place = result.get("place") or {}
    token_row = result.get("token_row") or {}
    name = escape(str(place.get("name") or f"ID {place_id}"))
    token = escape(str(token_row.get("token") or "—"))
    expires_at = escape(str(token_row.get("expires_at") or "—"))

    text = (
        "🔐 <b>Код прив'язки</b>\n\n"
        f"Заклад: <b>{name}</b>\n"
        f"Place ID: <code>{place_id}</code>\n\n"
        f"Token: <code>{token}</code>\n"
        f"Expires: <code>{expires_at}</code>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Згенерувати новий",
                    callback_data=f"{CB_BIZ_TOKV_PLACE_ROTATE_PREFIX}{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Заклади",
                    callback_data=f"{CB_BIZ_TOKV_PLACE_PAGE_PREFIX}{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_BIZ_TOK_MENU)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_TOKV_PLACE_ROTATE_PREFIX))
async def cb_biz_tokv_place_rotate(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Генерую…")
    parts = callback.data.split("|")
    if len(parts) < 5:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[1])
        service_id = int(parts[2])
        place_page = int(parts[3])
        service_page = int(parts[4])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        rotated = await business_service.rotate_claim_token_for_place(int(callback.from_user.id), place_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to rotate claim token for place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    name = escape(str((rotated.get("place") or {}).get("name") or f"ID {place_id}"))
    token = escape(str(rotated.get("token") or "—"))
    expires_at = escape(str(rotated.get("expires_at") or "—"))

    text = (
        "✅ <b>Новий код згенеровано</b>\n\n"
        f"Заклад: <b>{name}</b>\n"
        f"Place ID: <code>{place_id}</code>\n\n"
        f"Token: <code>{token}</code>\n"
        f"Expires: <code>{expires_at}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Заклади",
                    callback_data=f"{CB_BIZ_TOKV_PLACE_PAGE_PREFIX}{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_BIZ_TOK_MENU)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_TOKG_SVC_PAGE_PREFIX))
async def cb_biz_tokg_service_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_biz_token_services(
        callback.bot,
        callback.message.chat.id,
        page=page,
        mode="gen",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKG_SVC_PICK_PREFIX))
async def cb_biz_tokg_service_pick(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректна категорія", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        service_page = int(parts[2])
    except Exception:
        service_id = 0
        service_page = 0
    await _render_biz_token_places(
        callback.bot,
        callback.message.chat.id,
        service_id=service_id,
        place_page=0,
        service_page=service_page,
        mode="gen",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKG_PLACE_PAGE_PREFIX))
async def cb_biz_tokg_place_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 4:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        place_page = int(parts[2])
        service_page = int(parts[3])
    except Exception:
        service_id = 0
        place_page = 0
        service_page = 0
    await _render_biz_token_places(
        callback.bot,
        callback.message.chat.id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        mode="gen",
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_TOKG_PLACE_ROTATE_PREFIX))
async def cb_biz_tokg_place_rotate(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Генерую…")
    parts = callback.data.split("|")
    if len(parts) < 5:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[1])
        service_id = int(parts[2])
        place_page = int(parts[3])
        service_page = int(parts[4])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        rotated = await business_service.rotate_claim_token_for_place(int(callback.from_user.id), place_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to rotate claim token for place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    name = escape(str((rotated.get("place") or {}).get("name") or f"ID {place_id}"))
    token = escape(str(rotated.get("token") or "—"))
    expires_at = escape(str(rotated.get("expires_at") or "—"))

    text = (
        "✅ <b>Новий код згенеровано</b>\n\n"
        f"Заклад: <b>{name}</b>\n"
        f"Place ID: <code>{place_id}</code>\n\n"
        f"Token: <code>{token}</code>\n"
        f"Expires: <code>{expires_at}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Заклади",
                    callback_data=f"{CB_BIZ_TOKG_PLACE_PAGE_PREFIX}{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Згенерувати коди", callback_data=CB_BIZ_TOK_GEN)],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_BIZ_TOK_MENU)],
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


# =========================
# Business: Categories + Admin Create Place
# =========================


def _format_building_label(building: dict) -> str:
    return _truncate_label(f"{building.get('name', '—')} ({building.get('address', '—')})", 34)


def _full_building_address(building: dict, details: str) -> str:
    base = f"{building.get('name', '—')} ({building.get('address', '—')})"
    clean_details = str(details or "").strip()
    return base if not clean_details else f"{base}, {clean_details}"


async def _render_biz_categories(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    try:
        services = await business_repo.list_services()
    except Exception:
        logger.exception("Failed to load business categories")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження категорій.")
        return

    if not services:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Додати категорію", callback_data=CB_BIZ_CATEGORY_ADD)],
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
                [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
            ]
        )
        text = "🗂 <b>Категорії</b>\n\n"
        if note:
            text += f"{note}\n\n"
        text += "Категорій ще немає."
        await render(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=kb,
            prefer_message_id=prefer_message_id,
            force_new_message=True,
        )
        return

    total_pages = max(1, (len(services) + BIZ_SERVICES_PAGE_SIZE - 1) // BIZ_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * BIZ_SERVICES_PAGE_SIZE
    chunk = services[start : start + BIZ_SERVICES_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in chunk:
        try:
            places_count = await business_repo.count_places_by_service(int(svc["id"]))
        except Exception:
            places_count = 0
        label = _truncate_label(f"{svc['name']} ({places_count})", 30)
        buffer.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{CB_BIZ_CATEGORY_OPEN_PREFIX}{int(svc['id'])}|{safe_page}",
            )
        )
        if len(buffer) >= 2:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_CATEGORIES_PAGE_PREFIX}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_CATEGORIES_PAGE_PREFIX}{safe_page + 1}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="➕ Додати категорію", callback_data=CB_BIZ_CATEGORY_ADD)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    rows.append([InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")])

    text = "🗂 <b>Категорії</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть категорію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


async def _render_biz_category_detail(
    bot: Bot,
    chat_id: int,
    *,
    service_id: int,
    page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    service = await business_repo.get_service(int(service_id))
    if not service:
        await _render_biz_categories(
            bot,
            chat_id,
            page=page,
            prefer_message_id=prefer_message_id,
            note="❌ Категорію не знайдено.",
        )
        return
    try:
        places_count = await business_repo.count_places_by_service(int(service_id))
    except Exception:
        places_count = 0

    text = "🗂 <b>Категорія</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += (
        f"ID: <code>{int(service_id)}</code>\n"
        f"Назва: <b>{escape(str(service.get('name') or '—'))}</b>\n"
        f"Закладів: <b>{int(places_count)}</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Перейменувати", callback_data=f"{CB_BIZ_CATEGORY_RENAME_PREFIX}{int(service_id)}|{int(page)}")],
            [InlineKeyboardButton(text="➕ Додати заклад у цю категорію", callback_data=f"{CB_BIZ_CREATE_SVC_PICK_PREFIX}{int(service_id)}|{int(page)}")],
            [InlineKeyboardButton(text="« Категорії", callback_data=f"{CB_BIZ_CATEGORIES_PAGE_PREFIX}{int(page)}")],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data == CB_BIZ_CATEGORIES_MENU)
async def cb_biz_categories_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await _render_biz_categories(callback.bot, callback.message.chat.id, page=0, prefer_message_id=callback.message.message_id)


@router.callback_query(F.data.startswith(CB_BIZ_CATEGORIES_PAGE_PREFIX))
async def cb_biz_categories_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_biz_categories(callback.bot, callback.message.chat.id, page=page, prefer_message_id=callback.message.message_id)


@router.callback_query(F.data.startswith(CB_BIZ_CATEGORY_OPEN_PREFIX))
async def cb_biz_category_open(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳")
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    await _render_biz_category_detail(
        callback.bot,
        callback.message.chat.id,
        service_id=service_id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == CB_BIZ_CATEGORY_ADD)
async def cb_biz_category_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await state.set_state(BizCategoryCreateState.waiting_name)
    text = (
        "🗂 <b>Нова категорія</b>\n\n"
        "Надішліть назву категорії.\n"
        "Приклад: <code>Кав'ярні</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
            [InlineKeyboardButton(text="« Категорії", callback_data=CB_BIZ_CATEGORIES_MENU)],
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


@router.message(BizCategoryCreateState.waiting_name)
async def msg_biz_category_create_name(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    name = str(message.text or "").strip()
    if not name:
        await _render_biz_categories(
            message.bot,
            message.chat.id,
            page=0,
            prefer_message_id=None,
            note="❌ Порожня назва категорії.",
        )
        await state.clear()
        return
    try:
        result = await business_service.admin_create_service(int(message.from_user.id), name)
    except (BusinessValidationError, BusinessAccessDeniedError) as error:
        await _render_biz_categories(
            message.bot,
            message.chat.id,
            page=0,
            prefer_message_id=None,
            note=f"❌ {escape(str(error))}",
        )
        await state.clear()
        return
    except Exception:
        logger.exception("Failed to create business category")
        await _render_biz_categories(
            message.bot,
            message.chat.id,
            page=0,
            prefer_message_id=None,
            note="❌ Помилка створення категорії.",
        )
        await state.clear()
        return

    await state.clear()
    note = (
        f"✅ Додано категорію <b>{escape(result['name'])}</b>."
        if result.get("created")
        else f"ℹ️ Категорія <b>{escape(result['name'])}</b> вже існує."
    )
    await _render_biz_categories(message.bot, message.chat.id, page=0, prefer_message_id=None, note=note)


@router.callback_query(F.data.startswith(CB_BIZ_CATEGORY_RENAME_PREFIX))
async def cb_biz_category_rename(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    service = await business_repo.get_service(service_id)
    if not service:
        await callback.answer("Категорію не знайдено", show_alert=True)
        return
    await state.set_state(BizCategoryRenameState.waiting_name)
    await state.update_data(biz_category_service_id=service_id, biz_category_page=page)
    text = (
        "✏️ <b>Перейменування категорії</b>\n\n"
        f"Поточна назва: <b>{escape(str(service.get('name') or '—'))}</b>\n\n"
        "Надішліть нову назву."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
            [InlineKeyboardButton(text="« Категорія", callback_data=f"{CB_BIZ_CATEGORY_OPEN_PREFIX}{service_id}|{page}")],
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


@router.message(BizCategoryRenameState.waiting_name)
async def msg_biz_category_rename_name(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    data = await state.get_data()
    service_id = int(data.get("biz_category_service_id") or 0)
    page = int(data.get("biz_category_page") or 0)
    name = str(message.text or "").strip()
    if not service_id:
        await state.clear()
        await _render_biz_categories(
            message.bot,
            message.chat.id,
            page=page,
            prefer_message_id=None,
            note="❌ Категорію не вибрано.",
        )
        return
    try:
        result = await business_service.admin_rename_service(int(message.from_user.id), service_id, name)
    except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
        await state.clear()
        await _render_biz_category_detail(
            message.bot,
            message.chat.id,
            service_id=service_id,
            page=page,
            prefer_message_id=None,
            note=f"❌ {escape(str(error))}",
        )
        return
    except Exception:
        logger.exception("Failed to rename category %s", service_id)
        await state.clear()
        await _render_biz_category_detail(
            message.bot,
            message.chat.id,
            service_id=service_id,
            page=page,
            prefer_message_id=None,
            note="❌ Помилка оновлення категорії.",
        )
        return
    await state.clear()
    await _render_biz_category_detail(
        message.bot,
        message.chat.id,
        service_id=service_id,
        page=page,
        prefer_message_id=None,
        note=f"✅ Назву оновлено: <b>{escape(result['name'])}</b>",
    )


async def _render_biz_create_place_service_picker(
    bot: Bot,
    chat_id: int,
    *,
    page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    try:
        services = await business_repo.list_services()
    except Exception:
        logger.exception("Failed to load services for place create")
        await _render_business_menu(bot, chat_id, prefer_message_id=prefer_message_id, note="❌ Помилка завантаження категорій.")
        return

    if not services:
        await _render_biz_categories(
            bot,
            chat_id,
            page=0,
            prefer_message_id=prefer_message_id,
            note="Спочатку додайте хоча б одну категорію.",
        )
        return

    total_pages = max(1, (len(services) + BIZ_SERVICES_PAGE_SIZE - 1) // BIZ_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * BIZ_SERVICES_PAGE_SIZE
    chunk = services[start : start + BIZ_SERVICES_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in chunk:
        buffer.append(
            InlineKeyboardButton(
                text=_truncate_label(str(svc.get("name") or f"ID {svc.get('id')}"), 30),
                callback_data=f"{CB_BIZ_CREATE_SVC_PICK_PREFIX}{int(svc['id'])}|{safe_page}",
            )
        )
        if len(buffer) >= 2:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_CREATE_SVC_PAGE_PREFIX}{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_CREATE_SVC_PAGE_PREFIX}{safe_page + 1}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🗂 Категорії", callback_data=CB_BIZ_CATEGORIES_MENU)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    text = "➕ <b>Створення закладу</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть категорію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


async def _render_biz_create_building_picker(
    bot: Bot,
    chat_id: int,
    *,
    service_page: int,
    prefer_message_id: int | None,
) -> None:
    buildings = await business_repo.list_buildings()
    rows: list[list[InlineKeyboardButton]] = []
    for b in buildings:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_format_building_label(b),
                    callback_data=f"{CB_BIZ_CREATE_BUILDING_PICK_PREFIX}{int(b['id'])}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Категорії", callback_data=f"{CB_BIZ_CREATE_SVC_PAGE_PREFIX}{int(service_page)}")])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])
    await render(
        bot,
        chat_id=chat_id,
        text="📍 <b>Оберіть будинок</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


def _build_create_promo_keyboard(place_id: int, service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 Light (1 міс)", callback_data=f"{CB_BIZ_CREATE_PROMO_PREFIX}{place_id}|{service_id}|light"),
                InlineKeyboardButton(text="🎁 Pro (1 міс)", callback_data=f"{CB_BIZ_CREATE_PROMO_PREFIX}{place_id}|{service_id}|pro"),
            ],
            [
                InlineKeyboardButton(text="🎁 Partner (1 міс)", callback_data=f"{CB_BIZ_CREATE_PROMO_PREFIX}{place_id}|{service_id}|partner"),
                InlineKeyboardButton(text="Без промо", callback_data=f"{CB_BIZ_CREATE_PROMO_PREFIX}{place_id}|{service_id}|free"),
            ],
            [InlineKeyboardButton(text="🏢 Відкрити заклад", callback_data=f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}all|{place_id}|{service_id}|0|0")],
            [InlineKeyboardButton(text="➕ Додати ще заклад", callback_data=CB_BIZ_CREATE_PLACE_MENU)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
        ]
    )


@router.callback_query(F.data == CB_BIZ_CREATE_PLACE_MENU)
async def cb_biz_create_place_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await _render_biz_create_place_service_picker(
        callback.bot,
        callback.message.chat.id,
        page=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_CREATE_SVC_PAGE_PREFIX))
async def cb_biz_create_place_service_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        page = int(callback.data.split("|", 1)[1])
    except Exception:
        page = 0
    await _render_biz_create_place_service_picker(
        callback.bot,
        callback.message.chat.id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_CREATE_SVC_PICK_PREFIX))
async def cb_biz_create_place_service_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[1])
        service_page = int(parts[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    service = await business_repo.get_service(service_id)
    if not service:
        await callback.answer("Категорію не знайдено", show_alert=True)
        return

    await state.set_state(BizPlaceCreateState.waiting_name)
    await state.update_data(
        biz_create_service_id=service_id,
        biz_create_service_page=service_page,
        biz_create_service_name=str(service.get("name") or ""),
    )
    text = (
        "➕ <b>Створення закладу</b>\n\n"
        f"Категорія: <b>{escape(str(service.get('name') or '—'))}</b>\n\n"
        "Надішліть назву закладу."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
            [InlineKeyboardButton(text="« Категорії", callback_data=f"{CB_BIZ_CREATE_SVC_PAGE_PREFIX}{service_page}")],
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


@router.message(BizPlaceCreateState.waiting_name)
async def msg_biz_create_place_name(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    name = str(message.text or "").strip()
    if not name:
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note="❌ Порожня назва закладу.")
        await state.clear()
        return
    await state.update_data(biz_create_place_name=name)
    await state.set_state(BizPlaceCreateState.waiting_description)
    text = (
        "➕ <b>Створення закладу</b>\n\n"
        f"Назва: <b>{escape(name)}</b>\n\n"
        "Надішліть короткий опис закладу.\n"
        "Якщо опис не потрібен — надішліть <code>-</code>."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")]])
    await render(message.bot, chat_id=message.chat.id, text=text, reply_markup=kb, force_new_message=True)


@router.message(BizPlaceCreateState.waiting_description)
async def msg_biz_create_place_description(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    description = str(message.text or "").strip()
    if description in {"-", "—"}:
        description = ""
    await state.update_data(biz_create_description=description)
    data = await state.get_data()
    service_page = int(data.get("biz_create_service_page") or 0)
    await state.set_state(BizPlaceCreateState.waiting_address_details)
    await _render_biz_create_building_picker(
        message.bot,
        message.chat.id,
        service_page=service_page,
        prefer_message_id=None,
    )


@router.callback_query(F.data.startswith(CB_BIZ_CREATE_BUILDING_PICK_PREFIX))
async def cb_biz_create_place_building_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    try:
        building_id = int(callback.data.split("|", 1)[1])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    building = await business_repo.get_building(building_id)
    if not building:
        await callback.answer("Будинок не знайдено", show_alert=True)
        return
    await state.update_data(biz_create_building_id=building_id)
    building_title = _full_building_address(building, "")
    text = (
        "📍 <b>Деталізація адреси</b>\n\n"
        f"Будинок: <b>{escape(building_title)}</b>\n\n"
        "Надішліть деталі адреси.\n"
        "Приклад: <code>зі сторони Бермінгема, -1 поверх</code>\n"
        "Якщо без деталей — надішліть <code>-</code>."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")]])
    await render(
        callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=kb,
        prefer_message_id=callback.message.message_id,
        force_new_message=True,
    )


@router.message(BizPlaceCreateState.waiting_address_details)
async def msg_biz_create_place_address_details(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    details = str(message.text or "").strip()
    if details in {"-", "—"}:
        details = ""
    data = await state.get_data()
    service_id = int(data.get("biz_create_service_id") or 0)
    name = str(data.get("biz_create_place_name") or "")
    description = str(data.get("biz_create_description") or "")
    building_id = int(data.get("biz_create_building_id") or 0)

    if not service_id or not name or not building_id:
        await state.clear()
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note="❌ Дані створення втрачені. Спробуй ще раз.")
        return
    try:
        place = await business_service.admin_create_place(
            int(message.from_user.id),
            service_id=service_id,
            name=name,
            description=description,
            building_id=building_id,
            address_details=details,
            is_published=1,
        )
    except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
        await state.clear()
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note=f"❌ {escape(str(error))}")
        return
    except Exception:
        logger.exception("Failed to create place from admin")
        await state.clear()
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note="❌ Помилка створення закладу.")
        return

    await state.clear()
    place_id = int(place.get("id") or 0)
    service_id = int(place.get("service_id") or service_id)
    text = (
        "✅ <b>Заклад створено</b>\n\n"
        f"Назва: <b>{escape(str(place.get('name') or name))}</b>\n"
        f"Адреса: {escape(str(place.get('address') or '—'))}\n\n"
        "За потреби відразу признач промо‑підписку на 1 місяць:"
    )
    await render(
        message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=_build_create_promo_keyboard(place_id, service_id),
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_CREATE_PROMO_PREFIX))
async def cb_biz_create_place_promo(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳")
    parts = callback.data.split("|")
    if len(parts) < 4:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[1])
        service_id = int(parts[2])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    tier = str(parts[3] or "free").strip().lower()
    note = "ℹ️ Промо не застосовано."
    if tier != "free":
        try:
            await business_service.admin_set_subscription_tier(
                int(callback.from_user.id),
                place_id=place_id,
                tier=tier,
                months=1,
            )
            note = f"✅ Промо‑тариф <b>{escape(_subscription_tier_title(tier))}</b> активовано на 1 місяць."
        except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
            note = f"❌ {escape(str(error))}"
        except Exception:
            logger.exception("Failed to apply promo tier after place create")
            note = "❌ Не вдалося застосувати промо‑тариф."

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code="all",
        place_id=place_id,
        service_id=service_id,
        place_page=0,
        service_page=0,
        prefer_message_id=callback.message.message_id,
        note=note,
    )


# =========================
# Business: Places (Publish/Drafts)
# =========================


_BIZ_PLACES_FILTER_TITLES = {
    "unpub": "📝 Чернетки",
    "pub": "✅ Опубліковані",
    "all": "📚 Усі",
}


def _biz_places_filter_title(filter_code: str) -> str:
    return str(_BIZ_PLACES_FILTER_TITLES.get(str(filter_code or "").strip().lower(), filter_code or "all"))


def _biz_places_filter_to_is_published(filter_code: str) -> int | None:
    code = str(filter_code or "").strip().lower()
    if code == "pub":
        return 1
    if code == "unpub":
        return 0
    if code == "all":
        return None
    return 0


def _biz_places_filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_BIZ_PLACES_FILTER_TITLES["unpub"], callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}unpub"),
                InlineKeyboardButton(text=_BIZ_PLACES_FILTER_TITLES["pub"], callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}pub"),
            ],
            [InlineKeyboardButton(text=_BIZ_PLACES_FILTER_TITLES["all"], callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}all")],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
            [InlineKeyboardButton(text="« Головне меню", callback_data="admin_refresh")],
        ]
    )


async def _render_biz_places_filters(bot: Bot, chat_id: int, *, prefer_message_id: int | None, note: str | None = None) -> None:
    text = "🏢 <b>Заклади</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += "Оберіть фільтр:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=_biz_places_filters_keyboard(),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data == CB_BIZ_PLACES_MENU)
async def cb_biz_places_menu(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    await _render_biz_places_filters(callback.bot, callback.message.chat.id, prefer_message_id=callback.message.message_id)


async def _render_biz_places_services(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    page: int,
    prefer_message_id: int | None,
) -> None:
    is_published = _biz_places_filter_to_is_published(filter_code)
    try:
        services = await business_repo.list_services_with_place_counts_filtered(is_published=is_published)
    except Exception:
        logger.exception("Failed to list services for places filter=%s", filter_code)
        await _render_biz_places_filters(
            bot,
            chat_id,
            prefer_message_id=prefer_message_id,
            note="❌ Не вдалося завантажити категорії.",
        )
        return

    if not services:
        await _render_biz_places_filters(
            bot,
            chat_id,
            prefer_message_id=prefer_message_id,
            note="Немає закладів для цього фільтра.",
        )
        return

    total_pages = max(1, (len(services) + BIZ_SERVICES_PAGE_SIZE - 1) // BIZ_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * BIZ_SERVICES_PAGE_SIZE
    chunk = services[start : start + BIZ_SERVICES_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in chunk:
        label = _format_service_button(svc)
        cb = f"{CB_BIZ_PLACES_SVC_PICK_PREFIX}{filter_code}|{int(svc['id'])}|{safe_page}"
        buffer.append(InlineKeyboardButton(text=label, callback_data=cb))
        if len(buffer) >= 2:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_PLACES_SVC_PAGE_PREFIX}{filter_code}|{safe_page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_PLACES_SVC_PAGE_PREFIX}{filter_code}|{safe_page + 1}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔎 Пошук", callback_data=f"{CB_BIZ_PLACES_SEARCH_PREFIX}{filter_code}")])
    rows.append([InlineKeyboardButton(text="« Фільтр", callback_data=CB_BIZ_PLACES_MENU)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    title = _biz_places_filter_title(filter_code)
    text = f"🏢 <b>Заклади</b>\n\nФільтр: <b>{escape(str(title))}</b>\nОберіть категорію:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_FILTER_PREFIX))
async def cb_biz_places_filter(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    filter_code = callback.data.split("|", 1)[1] if "|" in callback.data else "unpub"
    await _render_biz_places_services(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        page=0,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_SVC_PAGE_PREFIX))
async def cb_biz_places_service_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 3:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        page = int(parts[2])
    except Exception:
        page = 0
    await _render_biz_places_services(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        page=page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_SEARCH_PREFIX))
async def cb_biz_places_search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    filter_code = callback.data.split("|", 1)[1] if "|" in callback.data else "all"
    title = escape(_biz_places_filter_title(filter_code))
    await state.set_state(BizPlacesSearchState.waiting_query)
    await state.update_data(biz_places_search_filter=filter_code)
    text = (
        "🔎 <b>Пошук закладу</b>\n\n"
        f"Фільтр: <b>{title}</b>\n\n"
        "Надішліть <code>place_id</code> або частину назви/адреси.\n"
        "Приклад: <code>123</code> або <code>coffee</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« До закладів", callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}{filter_code}")],
            [InlineKeyboardButton(text="« Фільтр", callback_data=CB_BIZ_PLACES_MENU)],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.message(BizPlacesSearchState.waiting_query)
async def msg_biz_places_search_query(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    query = str(message.text or "").strip()
    data = await state.get_data()
    filter_code = str(data.get("biz_places_search_filter") or "all")
    is_published = _biz_places_filter_to_is_published(filter_code)
    title = escape(_biz_places_filter_title(filter_code))

    if not query:
        text = (
            "🔎 <b>Пошук закладу</b>\n\n"
            f"Фільтр: <b>{title}</b>\n\n"
            "❌ Порожній запит. Надішліть <code>place_id</code> або частину назви/адреси."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« До закладів", callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}{filter_code}")],
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
            ]
        )
        await render(
            message.bot,
            chat_id=message.chat.id,
            text=text,
            reply_markup=kb,
            force_new_message=True,
        )
        return

    try:
        places = await business_repo.search_places_filtered(query, is_published=is_published, limit=20)
    except Exception:
        logger.exception("Failed to search places for query=%r filter=%s", query, filter_code)
        await state.clear()
        await _render_biz_places_filters(
            message.bot,
            message.chat.id,
            prefer_message_id=None,
            note="❌ Не вдалося виконати пошук.",
        )
        return

    await state.clear()

    if not places:
        text = (
            "🔎 <b>Пошук закладу</b>\n\n"
            f"Фільтр: <b>{title}</b>\n"
            f"Запит: <code>{escape(query)}</code>\n\n"
            "Нічого не знайдено."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔁 Новий пошук", callback_data=f"{CB_BIZ_PLACES_SEARCH_PREFIX}{filter_code}")],
                [InlineKeyboardButton(text="« До закладів", callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}{filter_code}")],
                [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
            ]
        )
        await render(
            message.bot,
            chat_id=message.chat.id,
            text=text,
            reply_markup=kb,
            force_new_message=True,
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for place in places:
        place_id = int(place.get("id") or 0)
        service_id = int(place.get("service_id") or 0)
        published = int(place.get("is_published") or 0) == 1
        prefix = "✅" if published else "📝"
        label = _truncate_label(f"{prefix} {place.get('name') or f'ID {place_id}'}", 40)
        cb = f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{place_id}|{service_id}|0|0"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    rows.append([InlineKeyboardButton(text="🔁 Новий пошук", callback_data=f"{CB_BIZ_PLACES_SEARCH_PREFIX}{filter_code}")])
    rows.append([InlineKeyboardButton(text="« До закладів", callback_data=f"{CB_BIZ_PLACES_FILTER_PREFIX}{filter_code}")])
    rows.append([InlineKeyboardButton(text="« Фільтр", callback_data=CB_BIZ_PLACES_MENU)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    text = (
        "🔎 <b>Результати пошуку</b>\n\n"
        f"Фільтр: <b>{title}</b>\n"
        f"Запит: <code>{escape(query)}</code>\n"
        f"Знайдено: <b>{len(places)}</b>"
    )
    await render(
        message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        force_new_message=True,
    )


async def _render_biz_places_list(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    service_id: int,
    place_page: int,
    service_page: int,
    prefer_message_id: int | None,
) -> None:
    is_published = _biz_places_filter_to_is_published(filter_code)
    try:
        total = await business_repo.count_places_by_service_filtered(int(service_id), is_published=is_published)
        total_pages = max(1, (total + BIZ_PLACES_PAGE_SIZE - 1) // BIZ_PLACES_PAGE_SIZE)
        safe_page = max(0, min(int(place_page), total_pages - 1))
        offset = safe_page * BIZ_PLACES_PAGE_SIZE
        places = await business_repo.list_places_by_service_filtered(
            int(service_id),
            is_published=is_published,
            limit=BIZ_PLACES_PAGE_SIZE,
            offset=offset,
        )
    except Exception:
        logger.exception("Failed to list places for service=%s filter=%s", service_id, filter_code)
        await _render_biz_places_services(
            bot,
            chat_id,
            filter_code=filter_code,
            page=service_page,
            prefer_message_id=prefer_message_id,
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for p in places:
        pid = int(p.get("id") or 0)
        published = int(p.get("is_published") or 0) == 1
        prefix = "✅" if published else "📝"
        label = _truncate_label(f"{prefix} {p.get('name') or f'ID {pid}'}", 40)
        cb = f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{pid}|{int(service_id)}|{safe_page}|{int(service_page)}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_PAGE_PREFIX}{filter_code}|{int(service_id)}|{safe_page - 1}|{int(service_page)}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data=CB_ADMIN_NOOP))
        if safe_page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_PAGE_PREFIX}{filter_code}|{int(service_id)}|{safe_page + 1}|{int(service_page)}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔎 Пошук", callback_data=f"{CB_BIZ_PLACES_SEARCH_PREFIX}{filter_code}")])
    rows.append([InlineKeyboardButton(text="« Категорії", callback_data=f"{CB_BIZ_PLACES_SVC_PAGE_PREFIX}{filter_code}|{int(service_page)}")])
    rows.append([InlineKeyboardButton(text="« Фільтр", callback_data=CB_BIZ_PLACES_MENU)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    title = _biz_places_filter_title(filter_code)
    text = f"🏢 <b>Заклади</b>\n\nФільтр: <b>{escape(str(title))}</b>\nОберіть заклад:"
    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_SVC_PICK_PREFIX))
async def cb_biz_places_service_pick(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 4:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        service_id = int(parts[2])
        service_page = int(parts[3])
    except Exception:
        service_id = 0
        service_page = 0
    await _render_biz_places_list(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        service_id=service_id,
        place_page=0,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_PLACE_PAGE_PREFIX))
async def cb_biz_places_place_page(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 5:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        service_id = int(parts[2])
        place_page = int(parts[3])
        service_page = int(parts[4])
    except Exception:
        service_id = 0
        place_page = 0
        service_page = 0
    await _render_biz_places_list(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


async def _render_biz_place_detail(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    place_id: int,
    service_id: int,
    place_page: int,
    service_page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    try:
        place = await business_repo.get_place(int(place_id))
    except Exception:
        logger.exception("Failed to load place %s", place_id)
        place = None
    if not place:
        await _render_biz_places_list(
            bot,
            chat_id,
            filter_code=filter_code,
            service_id=service_id,
            place_page=place_page,
            service_page=service_page,
            prefer_message_id=prefer_message_id,
        )
        return

    pending_owner: dict[str, object] | None = None
    try:
        pending_owner = await business_service.get_pending_owner_request_for_place(int(chat_id), int(place_id))
    except BusinessAccessDeniedError:
        pending_owner = None
    except Exception:
        logger.exception("Failed to load pending owner request for place %s", place_id)
        pending_owner = None

    published = int(place.get("is_published") or 0) == 1
    published_label = "✅ Опублікований" if published else "📝 Чернетка (не видна мешканцям)"

    name = escape(str(place.get("name") or "—"))
    addr = escape(str(place.get("address") or "—"))
    svc_name = escape(str(place.get("service_name") or "—"))
    biz_enabled = "ON" if int(place.get("business_enabled") or 0) else "OFF"
    verified = "✅" if int(place.get("is_verified") or 0) else "—"
    tier = escape(str(place.get("verified_tier") or "—"))
    until = escape(str(place.get("verified_until") or "—"))

    text = "🏢 <b>Заклад</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += (
        f"ID: <code>{int(place_id)}</code>\n"
        f"Категорія: <b>{svc_name}</b>\n"
        f"Статус: {published_label}\n"
        f"Business enabled: <b>{biz_enabled}</b>\n"
        f"Verified: <b>{verified}</b> (tier: <code>{tier}</code>, until: <code>{until}</code>)\n\n"
        f"Назва: <b>{name}</b>\n"
        f"Адреса: {addr}"
    )

    if pending_owner:
        owner_id = int(pending_owner.get("owner_id") or 0)
        owner_contact = _format_tg_contact(
            tg_user_id=pending_owner.get("tg_user_id"),
            username=pending_owner.get("username"),
            first_name=pending_owner.get("first_name"),
        )
        text += (
            "\n\n🛡 Pending owner request:\n"
            f"request: <code>{owner_id}</code>\n"
            f"user: {owner_contact}\n"
            f"created: <code>{escape(str(pending_owner.get('created_at') or ''))}</code>"
        )

    rows: list[list[InlineKeyboardButton]] = []
    if published:
        rows.append([InlineKeyboardButton(text="🙈 Приховати (unpublish)", callback_data=f"{CB_BIZ_PLACES_HIDE_PREFIX}{filter_code}|{int(place_id)}|{int(service_id)}|{int(place_page)}|{int(service_page)}")])
    else:
        rows.append([InlineKeyboardButton(text="✅ Опублікувати", callback_data=f"{CB_BIZ_PLACES_PUBLISH_PREFIX}{filter_code}|{int(place_id)}|{int(service_id)}|{int(place_page)}|{int(service_page)}")])
        rows.append([InlineKeyboardButton(text="🗑 Видалити чернетку", callback_data=f"{CB_BIZ_PLACES_DELETE_PREFIX}{filter_code}|{int(place_id)}|{int(service_id)}|{int(place_page)}|{int(service_page)}")])
    if pending_owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Відхилити owner-заявку",
                    callback_data=(
                        f"{CB_BIZ_PLACES_REJECT_OWNER_PREFIX}{filter_code}|{int(place_id)}|{int(service_id)}|"
                        f"{int(place_page)}|{int(service_page)}|{int(pending_owner.get('owner_id') or 0)}"
                    ),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Редагувати",
                callback_data=(
                    f"{CB_BIZ_PLACES_EDIT_MENU_PREFIX}{filter_code}|{int(place_id)}|"
                    f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                ),
            ),
            InlineKeyboardButton(
                text="🎁 Промо (1 міс)",
                callback_data=(
                    f"{CB_BIZ_PLACES_PROMO_MENU_PREFIX}{filter_code}|{int(place_id)}|"
                    f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                ),
            ),
        ]
    )

    rows.append([InlineKeyboardButton(text="« Заклади", callback_data=f"{CB_BIZ_PLACES_PLACE_PAGE_PREFIX}{filter_code}|{int(service_id)}|{int(place_page)}|{int(service_page)}")])
    rows.append([InlineKeyboardButton(text="« Категорії", callback_data=f"{CB_BIZ_PLACES_SVC_PAGE_PREFIX}{filter_code}|{int(service_page)}")])
    rows.append([InlineKeyboardButton(text="« Фільтр", callback_data=CB_BIZ_PLACES_MENU)])
    rows.append([InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)])

    await render(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_PLACE_OPEN_PREFIX))
async def cb_biz_places_place_open(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳")
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


async def _render_biz_place_edit_menu(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    place_id: int,
    service_id: int,
    place_page: int,
    service_page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    place = await business_repo.get_place(int(place_id))
    if not place:
        await _render_biz_places_list(
            bot,
            chat_id,
            filter_code=filter_code,
            service_id=service_id,
            place_page=place_page,
            service_page=service_page,
            prefer_message_id=prefer_message_id,
        )
        return

    text = "✏️ <b>Редагування закладу</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += (
        f"Заклад: <b>{escape(str(place.get('name') or f'ID {int(place_id)}'))}</b>\n"
        "Що хочете змінити?"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Назва",
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_FIELD_PREFIX}name|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
                InlineKeyboardButton(
                    text="📝 Опис",
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_FIELD_PREFIX}description|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📍 Адреса",
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_FIELD_PREFIX}address|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                )
            ],
            [InlineKeyboardButton(text="« Заклади", callback_data=f"{CB_BIZ_PLACES_PLACE_PAGE_PREFIX}{filter_code}|{int(service_id)}|{int(place_page)}|{int(service_page)}")],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


async def _render_biz_place_edit_building_picker(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    place_id: int,
    service_id: int,
    place_page: int,
    service_page: int,
    prefer_message_id: int | None,
) -> None:
    buildings = await business_repo.list_buildings()
    rows: list[list[InlineKeyboardButton]] = []
    for building in buildings:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_format_building_label(building),
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_BUILDING_PREFIX}{int(building['id'])}|{filter_code}|"
                        f"{int(place_id)}|{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="« Назад",
                callback_data=(
                    f"{CB_BIZ_PLACES_EDIT_MENU_PREFIX}{filter_code}|{int(place_id)}|"
                    f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                ),
            )
        ]
    )
    await render(
        bot,
        chat_id=chat_id,
        text="📍 <b>Оберіть будинок</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        prefer_message_id=prefer_message_id,
        force_new_message=True,
    )


async def _render_biz_place_promo_menu(
    bot: Bot,
    chat_id: int,
    *,
    filter_code: str,
    place_id: int,
    service_id: int,
    place_page: int,
    service_page: int,
    prefer_message_id: int | None,
    note: str | None = None,
) -> None:
    place = await business_repo.get_place(int(place_id))
    if not place:
        await _render_biz_places_list(
            bot,
            chat_id,
            filter_code=filter_code,
            service_id=service_id,
            place_page=place_page,
            service_page=service_page,
            prefer_message_id=prefer_message_id,
        )
        return
    subscription = await business_repo.ensure_subscription(int(place_id))
    tier = _subscription_tier_title(str(subscription.get("tier") or "free"))
    status = _subscription_status_title(str(subscription.get("status") or "inactive"))
    expires_at = escape(str(subscription.get("expires_at") or "—"))

    text = "🎁 <b>Промо‑підписка (1 місяць)</b>\n\n"
    if note:
        text += f"{note}\n\n"
    text += (
        f"Заклад: <b>{escape(str(place.get('name') or f'ID {int(place_id)}'))}</b>\n"
        f"Поточний тариф: <b>{escape(tier)}</b>\n"
        f"Статус: {status}\n"
        f"До: <code>{expires_at}</code>\n\n"
        "Оберіть тариф, який застосувати на 1 місяць:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Light",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PROMO_SET_PREFIX}light|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
                InlineKeyboardButton(
                    text="Pro",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PROMO_SET_PREFIX}pro|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Partner",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PROMO_SET_PREFIX}partner|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
                InlineKeyboardButton(
                    text="Free",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PROMO_SET_PREFIX}free|{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=(
                        f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{int(place_id)}|"
                        f"{int(service_id)}|{int(place_page)}|{int(service_page)}"
                    ),
                )
            ],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_EDIT_MENU_PREFIX))
async def cb_biz_places_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    await _render_biz_place_edit_menu(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_EDIT_FIELD_PREFIX))
async def cb_biz_places_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 7:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    field = str(parts[1] or "").strip().lower()
    filter_code = parts[2]
    try:
        place_id = int(parts[3])
        service_id = int(parts[4])
        place_page = int(parts[5])
        service_page = int(parts[6])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    if field not in {"name", "description", "address"}:
        await callback.answer("❌ Поле недоступне", show_alert=True)
        return

    if field == "address":
        await state.clear()
        await _render_biz_place_edit_building_picker(
            callback.bot,
            callback.message.chat.id,
            filter_code=filter_code,
            place_id=place_id,
            service_id=service_id,
            place_page=place_page,
            service_page=service_page,
            prefer_message_id=callback.message.message_id,
        )
        return

    await state.set_state(BizPlaceEditState.waiting_value)
    await state.update_data(
        biz_edit_place_field=field,
        biz_edit_place_filter=filter_code,
        biz_edit_place_id=place_id,
        biz_edit_service_id=service_id,
        biz_edit_place_page=place_page,
        biz_edit_service_page=service_page,
    )
    field_title = "назву" if field == "name" else "опис"
    text = (
        "✏️ <b>Редагування закладу</b>\n\n"
        f"Надішліть нову {field_title}."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_MENU_PREFIX}{filter_code}|{place_id}|{service_id}|"
                        f"{place_page}|{service_page}"
                    ),
                )
            ],
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


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_EDIT_BUILDING_PREFIX))
async def cb_biz_places_edit_building_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 7:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    try:
        building_id = int(parts[1])
        filter_code = parts[2]
        place_id = int(parts[3])
        service_id = int(parts[4])
        place_page = int(parts[5])
        service_page = int(parts[6])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    building = await business_repo.get_building(building_id)
    if not building:
        await callback.answer("Будинок не знайдено", show_alert=True)
        return

    await state.set_state(BizPlaceEditState.waiting_address_details)
    await state.update_data(
        biz_edit_place_field="address",
        biz_edit_place_filter=filter_code,
        biz_edit_place_id=place_id,
        biz_edit_service_id=service_id,
        biz_edit_place_page=place_page,
        biz_edit_service_page=service_page,
        biz_edit_place_building_id=building_id,
    )
    text = (
        "📍 <b>Нова адреса</b>\n\n"
        f"Будинок: <b>{escape(_full_building_address(building, ''))}</b>\n\n"
        "Надішліть деталізацію адреси.\n"
        "Приклад: <code>зі сторони Бермінгема, -1 поверх</code>\n"
        "Якщо без деталей — надішліть <code>-</code>."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_cancel")],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=(
                        f"{CB_BIZ_PLACES_EDIT_MENU_PREFIX}{filter_code}|{place_id}|{service_id}|"
                        f"{place_page}|{service_page}"
                    ),
                )
            ],
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


@router.message(BizPlaceEditState.waiting_value)
async def msg_biz_place_edit_value(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    value = str(message.text or "").strip()
    data = await state.get_data()
    field = str(data.get("biz_edit_place_field") or "").strip().lower()
    filter_code = str(data.get("biz_edit_place_filter") or "all")
    place_id = int(data.get("biz_edit_place_id") or 0)
    service_id = int(data.get("biz_edit_service_id") or 0)
    place_page = int(data.get("biz_edit_place_page") or 0)
    service_page = int(data.get("biz_edit_service_page") or 0)

    if not place_id or field not in {"name", "description"}:
        await state.clear()
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note="❌ Сесія редагування втрачена.")
        return

    try:
        await business_service.admin_update_place_field(
            int(message.from_user.id),
            place_id=place_id,
            field=field,
            value=value,
        )
        note = "✅ Дані закладу оновлено."
    except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
        note = f"❌ {escape(str(error))}"
    except Exception:
        logger.exception("Failed to update place field from admin UI")
        note = "❌ Помилка оновлення."
    finally:
        await state.clear()

    await _render_biz_place_detail(
        message.bot,
        message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=None,
        note=note,
    )


@router.message(BizPlaceEditState.waiting_address_details)
async def msg_biz_place_edit_address_details(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await try_delete_user_message(message)
    details = str(message.text or "").strip()
    if details in {"-", "—"}:
        details = ""
    data = await state.get_data()
    filter_code = str(data.get("biz_edit_place_filter") or "all")
    place_id = int(data.get("biz_edit_place_id") or 0)
    service_id = int(data.get("biz_edit_service_id") or 0)
    place_page = int(data.get("biz_edit_place_page") or 0)
    service_page = int(data.get("biz_edit_service_page") or 0)
    building_id = int(data.get("biz_edit_place_building_id") or 0)
    if not place_id or not building_id:
        await state.clear()
        await _render_business_menu(message.bot, message.chat.id, prefer_message_id=None, note="❌ Сесія редагування втрачена.")
        return

    building = await business_repo.get_building(building_id)
    if not building:
        await state.clear()
        await _render_biz_place_detail(
            message.bot,
            message.chat.id,
            filter_code=filter_code,
            place_id=place_id,
            service_id=service_id,
            place_page=place_page,
            service_page=service_page,
            prefer_message_id=None,
            note="❌ Будинок не знайдено.",
        )
        return
    address = _full_building_address(building, details)
    try:
        await business_service.admin_update_place_field(
            int(message.from_user.id),
            place_id=place_id,
            field="address",
            value=address,
        )
        note = "✅ Адресу оновлено."
    except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
        note = f"❌ {escape(str(error))}"
    except Exception:
        logger.exception("Failed to update place address from admin UI")
        note = "❌ Помилка оновлення адреси."
    finally:
        await state.clear()

    await _render_biz_place_detail(
        message.bot,
        message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=None,
        note=note,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_PROMO_MENU_PREFIX))
async def cb_biz_places_promo_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    await _render_biz_place_promo_menu(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_PROMO_SET_PREFIX))
async def cb_biz_places_promo_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳")
    parts = callback.data.split("|")
    if len(parts) < 7:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    tier = str(parts[1] or "").strip().lower()
    filter_code = parts[2]
    try:
        place_id = int(parts[3])
        service_id = int(parts[4])
        place_page = int(parts[5])
        service_page = int(parts[6])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        await business_service.admin_set_subscription_tier(
            int(callback.from_user.id),
            place_id=place_id,
            tier=tier,
            months=1,
        )
        note = f"✅ Встановлено тариф <b>{escape(_subscription_tier_title(tier))}</b> на 1 місяць."
    except (BusinessValidationError, BusinessAccessDeniedError, BusinessNotFoundError) as error:
        note = f"❌ {escape(str(error))}"
    except Exception:
        logger.exception("Failed to set promo tier from admin UI")
        note = "❌ Не вдалося оновити тариф."

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
        note=note,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_PUBLISH_PREFIX))
async def cb_biz_places_publish(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Публікую…")
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        await business_service.set_place_published(int(callback.from_user.id), place_id, is_published=1)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to publish place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
        note="✅ Опубліковано.",
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_HIDE_PREFIX))
async def cb_biz_places_hide_confirm_screen(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    text = (
        "⚠️ <b>Підтвердження</b>\n\n"
        "Приховати цей заклад від мешканців?\n"
        "Він зникне з каталогу і з WebApp."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Так, приховати",
                    callback_data=f"{CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX}{filter_code}|{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX))
async def cb_biz_places_hide(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Приховую…")
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        await business_service.set_place_published(int(callback.from_user.id), place_id, is_published=0)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to unpublish place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
        note="✅ Приховано.",
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_DELETE_PREFIX))
async def cb_biz_places_delete_confirm_screen(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    text = (
        "⚠️ <b>Підтвердження</b>\n\n"
        "Видалити цю чернетку?\n"
        "Ця дія незворотна."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Так, видалити",
                    callback_data=f"{CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX}{filter_code}|{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX))
async def cb_biz_places_delete(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Видаляю…")
    parts = callback.data.split("|")
    if len(parts) < 6:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        await business_service.delete_place_draft(int(callback.from_user.id), place_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to delete draft place %s", place_id)
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await callback.answer("✅ Видалено")
    await _render_biz_places_list(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_REJECT_OWNER_PREFIX))
async def cb_biz_places_reject_owner_confirm_screen(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer()
    parts = callback.data.split("|")
    if len(parts) < 7:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
        owner_id = int(parts[6])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    text = (
        "⚠️ <b>Підтвердження</b>\n\n"
        "Відхилити pending owner‑заявку для цього закладу?\n"
        f"Owner request: <code>{owner_id}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Так, відхилити",
                    callback_data=(
                        f"{CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX}{filter_code}|{place_id}|{service_id}|"
                        f"{place_page}|{service_page}|{owner_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{CB_BIZ_PLACES_PLACE_OPEN_PREFIX}{filter_code}|{place_id}|{service_id}|{place_page}|{service_page}",
                )
            ],
            [InlineKeyboardButton(text="« Бізнес", callback_data=CB_BIZ_MENU)],
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


@router.callback_query(F.data.startswith(CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX))
async def cb_biz_places_reject_owner(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await callback.answer("⏳ Відхиляю…")
    parts = callback.data.split("|")
    if len(parts) < 7:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return
    filter_code = parts[1]
    try:
        place_id = int(parts[2])
        service_id = int(parts[3])
        place_page = int(parts[4])
        service_page = int(parts[5])
        owner_id = int(parts[6])
    except Exception:
        await callback.answer("❌ Некоректні дані", show_alert=True)
        return

    try:
        updated = await business_service.reject_owner_request(int(callback.from_user.id), owner_id)
    except (BusinessValidationError, BusinessNotFoundError, BusinessAccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to reject pending owner request from place detail")
        await callback.answer("❌ Помилка", show_alert=True)
        return

    await _notify_owner_via_business_bot(
        int(updated["tg_user_id"]),
        "❌ Твою заявку на керування бізнесом відхилено адміністратором.",
    )

    await _render_biz_place_detail(
        callback.bot,
        callback.message.chat.id,
        filter_code=filter_code,
        place_id=place_id,
        service_id=service_id,
        place_page=place_page,
        service_page=service_page,
        prefer_message_id=callback.message.message_id,
        note=f"✅ Owner-заявку <code>{owner_id}</code> відхилено.",
    )
