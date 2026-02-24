from aiogram import Router, F, BaseMiddleware
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, ReplyKeyboardRemove,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile, User
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os
import html
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict
import re

from config import CFG
from inline_special_queries import resolve_inline_special_result
from tg_buttons import STYLE_PRIMARY, STYLE_SUCCESS, ikb
from database import (
    add_subscriber, remove_subscriber, db_get, db_set, set_quiet_hours, get_quiet_hours,
    get_notification_settings, set_light_notifications, set_alert_notifications,
    set_schedule_notifications, get_sponsored_offers_enabled,
    set_sponsored_offers_enabled, sponsored_offers_enabled_key,
    get_offers_digest_enabled, set_offers_digest_enabled,
    has_any_published_verified_business_place,
    get_last_event, get_subscriber_building, get_building_by_id, save_last_bot_message
)
from services import state_text, calculate_stats, format_duration, format_light_status

router = Router()
logger = logging.getLogger(__name__)

RESIDENT_VERIFIED_TIER_TITLES = {
    "light": "Light",
    "pro": "Premium",
    "partner": "Partner",
}
TELEGRAM_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
PLAIN_HOST_WITH_PATH_RE = re.compile(
    r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?::\d{2,5})?(?:[/?#].*)?$"
)


def _parse_int_env(name: str) -> int | None:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


ADBOT_INTERNAL_CHAT_ID = _parse_int_env("ADBOT_INTERNAL_CHAT_ID")


def _chat_id_variants(chat_id: int) -> set[int]:
    """
    Return known equivalent chat-id representations between Bot API and Telethon:
    - Bot API supergroup/channel: -100XXXXXXXXXX
    - Telethon peer id:           -XXXXXXXXXX
    """
    value = int(chat_id or 0)
    variants: set[int] = {value}
    if value == 0:
        return variants

    abs_value = abs(value)
    raw = str(abs_value)

    # Bot API -> Telethon
    if value < 0 and raw.startswith("100") and len(raw) > 3:
        try:
            variants.add(-int(raw[3:]))
        except Exception:
            pass
        return variants

    # Telethon -> Bot API
    if value < 0:
        try:
            variants.add(-int(f"100{abs_value}"))
        except Exception:
            pass
    return variants


def _resident_verified_tier_title(raw_tier: str | None) -> str:
    normalized = str(raw_tier or "").strip().lower()
    return RESIDENT_VERIFIED_TIER_TITLES.get(normalized, normalized.upper())


def format_user_label(user: User | None, fallback_id: int | None = None) -> str:
    """Повертає читабельний формат користувача: @username (First Last) - id."""
    if not user:
        return str(fallback_id) if fallback_id is not None else "unknown"
    user_id = user.id
    username = user.username
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    name = " ".join([part for part in [first, last] if part]).strip()

    if username and name:
        return f"@{username} ({name}) - {user_id}"
    if username:
        return f"@{username} - {user_id}"
    if name:
        return f"{name} - {user_id}"
    return str(user_id)


async def maybe_autoclear_reply_keyboard(message: Message) -> None:
    """Разово прибрати стару ReplyKeyboard для користувача у режимі WebApp."""
    if not CFG.web_app_enabled:
        return
    if not message.from_user:
        return
    chat_id = message.chat.id
    key = f"replykbd_cleared:{chat_id}"
    if await db_get(key):
        return
    await remove_reply_keyboard(message)
    await db_set(key, "1")


async def _auto_answer_callback(callback: CallbackQuery, delay: float = 0.25) -> None:
    """Auto-answer callback after short delay to remove Telegram 'pending' state."""
    await asyncio.sleep(delay)
    try:
        await safe_callback_answer(callback)
    except Exception:
        pass


async def safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """
    Безпечно відповідає на callback_query.
    Ігнорує стандартні race-case помилки Telegram для протермінованих/вже-відповіданих query.
    """
    try:
        await callback.answer(*args, **kwargs)
    except Exception as exc:
        msg = str(exc).lower()
        if "query is too old" in msg or "query id is invalid" in msg:
            logger.debug("Ignore stale callback answer: %s", exc)
            return
        raise


def _ui_last_message_key(chat_id: int, context_key: str) -> str:
    return f"ui:last_msg_id:{int(chat_id)}:{str(context_key or 'default').strip()}"


async def _ui_get_last_message_id(chat_id: int, context_key: str) -> int | None:
    raw = await db_get(_ui_last_message_key(chat_id, context_key))
    if not raw:
        return None
    try:
        value = int(str(raw).strip())
    except Exception:
        return None
    return value if value > 0 else None


async def _ui_set_last_message_id(chat_id: int, context_key: str, message_id: int) -> None:
    if not chat_id or not message_id:
        return
    await db_set(_ui_last_message_key(chat_id, context_key), str(int(message_id)))


def _edit_error_reason(exc: Exception) -> str:
    msg = str(exc).lower()
    if "message to edit not found" in msg:
        return "message_deleted"
    if "message can't be edited" in msg:
        return "not_editable"
    return "edit_failed"


async def render_or_edit(
    *,
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    context_key: str,
    prefer_message_id: int | None = None,
    disable_web_page_preview: bool = True,
    force_new_message: bool = False,
) -> int:
    """Render resident interactive UI via edit-first strategy with one fallback send."""
    if not chat_id:
        raise ValueError("chat_id is required")

    stored_id = await _ui_get_last_message_id(chat_id, context_key)
    candidates: list[int] = []
    if prefer_message_id and int(prefer_message_id) > 0:
        candidates.append(int(prefer_message_id))
    if stored_id and int(stored_id) > 0 and int(stored_id) not in candidates:
        candidates.append(int(stored_id))

    last_reason = "race_recovered"
    if not force_new_message:
        for message_id in candidates:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
                await _ui_set_last_message_id(chat_id, context_key, message_id)
                await save_last_bot_message(chat_id, message_id)
                return int(message_id)
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    await _ui_set_last_message_id(chat_id, context_key, message_id)
                    await save_last_bot_message(chat_id, message_id)
                    return int(message_id)
                last_reason = _edit_error_reason(exc)
            except Exception as exc:
                last_reason = _edit_error_reason(exc)

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    await _ui_set_last_message_id(chat_id, context_key, int(sent.message_id))
    await save_last_bot_message(chat_id, int(sent.message_id))
    logger.info(
        "resident_ui render_or_edit fallback: reason=%s chat_id=%s context=%s message_id=%s",
        last_reason,
        chat_id,
        context_key,
        int(sent.message_id),
    )
    return int(sent.message_id)


class ReplyKeyboardAutoClearMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            await maybe_autoclear_reply_keyboard(event)
        return await handler(event, data)


router.message.middleware(ReplyKeyboardAutoClearMiddleware())


class CallbackAutoAnswerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            callback_data = str(event.data or "")
            # Do not pre-answer callbacks that should return URL/show_alert explicitly.
            no_auto_prefixes = (
                "pcoupon_",
                "pchat_",
                "pcall_",
                "plink_",
                "plogo_",
                "pmenu_",
                "porder_",
                "pmimg1_",
                "pmimg2_",
                "pph1_",
                "pph2_",
                "pph3_",
            )
            if not callback_data.startswith(no_auto_prefixes):
                asyncio.create_task(_auto_answer_callback(event))
        return await handler(event, data)


router.callback_query.middleware(CallbackAutoAnswerMiddleware())


async def remove_reply_keyboard(message: Message) -> None:
    """Намагаємось прибрати ReplyKeyboard без зайвих повідомлень у чаті."""
    try:
        # Відправляємо видиме повідомлення, щоб клієнти точно застосували ReplyKeyboardRemove,
        # потім прибираємо його, щоб не засмічувати чат.
        removal_msg = await message.answer(
            "Оновлюю меню…",
            reply_markup=ReplyKeyboardRemove(),
            disable_notification=True
        )
        try:
            await asyncio.sleep(0.8)
            await removal_msg.delete()
        except Exception:
            pass
    except Exception:
        pass


async def handle_webapp_reply_keyboard(message: Message) -> bool:
    """У режимі WebApp прибираємо застарілі ReplyKeyboard-повідомлення."""
    if not CFG.web_app_enabled:
        return False
    try:
        await message.delete()
    except Exception:
        pass
    await remove_reply_keyboard(message)
    building_text = await get_user_building_text(message.chat.id)
    light_status = await get_light_status_text(message.chat.id)
    alert_status = await get_alert_status_text()
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=await get_main_keyboard_for_user(message.chat.id),
        context_key="main_menu",
    )
    return True


# ============ FSM States для інтерактивного додавання закладу ============

class AddPlaceStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_address = State()
    waiting_for_keywords = State()


class PlaceReportStates(StatesGroup):
    waiting_for_text = State()


# Маппінг будинків до файлів карт (винесено для повторного використання)
BUILDING_MAPS = {
    "Честер (28-д)": "Честер 28-д.png",
    "Манчестер (26-г)": "Манчестер 26-г.png",
    "Лондон (28-е)": "Лондон 28-е.png",
    "Ньюкасл (24-в)": "Ньюкасл 24-в.png",
    "Брістоль (24-б)": "Брістоль 24-б.png",
    "Оксфорд (28-б)": "Оксфорд 28-б.png",
    "Кембрідж (26)": "Кембрідж 26.png",
    "Ліверпуль (24-а)": "Ліверпуль 24-а.png",
    "Бермінгем (26-б)": "Бермінгем 26-б.png",
    "Брайтон (26-в)": "Брайтон 26-в.png",
    "Лінкольн (28-к)": "Лінкольн 28-к.png",
    "Віндзор (26-д)": "Віндзор 26-д.png",
    "Ноттінгем (24-г)": "Ноттінгем 24-г.png",
    "Престон": "Престон.png",
    "Паркінг": "parking.png",
    "Комора": "komora.png",
}


def get_map_file_for_address(address: str | None) -> str | None:
    """Отримати шлях до файлу карти за адресою."""
    if not address:
        return None
    
    for building, map_name in BUILDING_MAPS.items():
        if building in address:
            map_path = os.path.join(os.path.dirname(__file__), "maps", map_name)
            if os.path.exists(map_path):
                return map_path
    return None


async def show_place_with_map(message: Message, place_id: int):
    """Показати заклад з картою для deep-link (`/start place_<id>`).

    Використовуємо той самий рендер, що і в каталозі закладів:
    лайк/анлайк, CTA-кнопки, офери, партнерські блоки та карту (якщо є).
    """
    user_id = int(message.chat.id)
    if message.from_user:
        try:
            user_id = int(message.from_user.id)
        except Exception:
            user_id = int(message.chat.id)

    shown = await _render_place_detail_message(message, place_id=place_id, user_id=user_id)
    if not shown:
        await message.answer("❌ Заклад не знайдено.")


async def get_user_building_text(user_id: int) -> str:
    """Отримати текст з назвою будинку користувача."""
    from database import get_subscriber_building_and_section

    building_id, section_id = await get_subscriber_building_and_section(user_id)
    if building_id:
        building = get_building_by_id(building_id) if building_id else None
        if building:
            if section_id:
                return f"🏢 Ваш будинок: {building['name']}, секція {section_id}"
            return f"🏢 Ваш будинок: {building['name']} (секцію не обрано)"
    return "🏢 Будинок/секцію не обрано"


async def get_alert_status_text() -> str:
    """Отримати текст статусу тривоги з кешу (без запиту до API)."""
    alert_state = await db_get("last_alert_state")
    if alert_state == "active":
        return "🚨 ТРИВОГА!"
    elif alert_state == "inactive":
        return "✅ Без тривоги"
    else:
        return "❓ Статус невідомий"


async def get_light_status_text(user_id: int) -> str:
    """
    Отримати короткий текст статусу світла для будинку користувача.
    
    Логіка: сенсор онлайн = світло є, сенсор офлайн = світла немає.
    """
    from database import (
        get_subscriber_building_and_section,
        get_sensors_by_building,
        default_section_for_building,
        is_valid_section_for_building,
    )
    
    user_building_id, user_section_id = await get_subscriber_building_and_section(user_id)
    if not user_building_id:
        return "💡 Світло: оберіть будинок"
    if not is_valid_section_for_building(user_building_id, user_section_id):
        return "💡 Світло: оберіть секцію"
    
    # Перевіряємо чи є сенсори
    sensors = await get_sensors_by_building(user_building_id)
    if not sensors:
        return "💡 Світло: немає даних"
    
    # Рахуємо онлайн сенсори для секції (онлайн = світло є)
    sensors_online = 0
    sensors_total = 0
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    for s in sensors:
        sid = s.get("section_id")
        if sid is None:
            sid = default_section_for_building(user_building_id)
        if sid != user_section_id:
            continue
        sensors_total += 1
        if s["last_heartbeat"] and (now - s["last_heartbeat"]) < timeout:
            sensors_online += 1

    if sensors_total == 0:
        return "💡 Світло: немає сенсора в секції"
    return "💡 Є світло" if sensors_online > 0 else "💡 Немає світла"
def get_main_keyboard() -> InlineKeyboardMarkup:
    """Головна клавіатура з основними діями."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏠 Обрати будинок", callback_data="select_building"),
        ],
        [
            InlineKeyboardButton(text="💡 Світло/опалення/вода", callback_data="utilities_menu"),
        ],
        [
            InlineKeyboardButton(text="🏢 Заклади в ЖК", callback_data="places_menu"),
            InlineKeyboardButton(text="🔍 Пошук закладу", callback_data="search_menu"),
        ],
        [
            InlineKeyboardButton(text="🚨 Тривоги та укриття", callback_data="alerts_menu"),
            InlineKeyboardButton(text="📞 Сервісна служба", callback_data="service_menu"),
        ],
        [
            InlineKeyboardButton(text="🔔 Сповіщення та тихі години", callback_data="notifications_menu"),
        ],
    ])


def _sponsored_enabled_key(chat_id: int) -> str:
    return sponsored_offers_enabled_key(chat_id)


def _sponsored_last_seen_day_key(chat_id: int) -> str:
    return f"sponsored_last_seen_day:{int(chat_id)}"


def _sponsored_seen_counter_key(chat_id: int) -> str:
    return f"sponsored_seen_counter:{int(chat_id)}"


SPONSORED_ROW_DAILY_LIMIT = 5


def _today_utc_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _parse_sponsored_counter(raw: str, *, today: str) -> int:
    value = str(raw or "").strip()
    if not value:
        return 0
    if "|" in value:
        day, count_raw = value.split("|", 1)
        if day.strip() != today:
            return 0
        try:
            parsed = int(count_raw.strip())
        except Exception:
            return 0
        return max(0, parsed)
    # Backward compatibility (legacy numeric format).
    try:
        parsed = int(value)
    except Exception:
        return 0
    return max(0, parsed)


async def _get_sponsored_seen_today(chat_id: int, *, today: str) -> int:
    raw_counter = str((await db_get(_sponsored_seen_counter_key(chat_id))) or "").strip()
    if raw_counter:
        return _parse_sponsored_counter(raw_counter, today=today)

    # Legacy fallback (once/day marker): treat as 1 already shown today.
    legacy_day = str((await db_get(_sponsored_last_seen_day_key(chat_id))) or "").strip()
    return 1 if legacy_day == today else 0


async def _mark_sponsored_seen(chat_id: int, *, today: str) -> int:
    current = await _get_sponsored_seen_today(chat_id, today=today)
    updated = current + 1
    await db_set(_sponsored_seen_counter_key(chat_id), f"{today}|{updated}")
    # Keep legacy key for compatibility with existing diagnostics/tools.
    await db_set(_sponsored_last_seen_day_key(chat_id), today)
    return updated


def _truncate_sponsored_place_name(name: str, max_len: int = 34) -> str:
    text = str(name or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


async def _is_sponsored_offers_enabled(chat_id: int) -> bool:
    return await get_sponsored_offers_enabled(chat_id)


async def _set_sponsored_offers_enabled(chat_id: int, enabled: bool) -> None:
    await set_sponsored_offers_enabled(chat_id, enabled)


async def _is_offers_digest_enabled(chat_id: int) -> bool:
    return await get_offers_digest_enabled(chat_id)


async def _set_offers_digest_enabled(chat_id: int, enabled: bool) -> None:
    await set_offers_digest_enabled(chat_id, enabled)


async def _is_business_offers_ui_visible() -> bool:
    """Monetization controls are visible only after first published verified place."""
    from business import is_business_feature_enabled

    if not is_business_feature_enabled():
        return False
    try:
        return await has_any_published_verified_business_place()
    except Exception:
        logger.exception("Failed to evaluate business offers UI visibility")
        return False


async def _pick_sponsored_partner_place() -> dict[str, Any] | None:
    """Choose partner place for sponsored row with deterministic rotation."""
    from business import is_business_feature_enabled
    from database import get_partner_places_for_sponsored

    if not is_business_feature_enabled():
        return None
    places = await get_partner_places_for_sponsored()
    if not places:
        return None

    raw_hours = str((await db_get("sponsored_rotation_hours")) or "").strip()
    try:
        rotation_hours = int(raw_hours)
    except Exception:
        rotation_hours = 48
    if rotation_hours <= 0:
        rotation_hours = 48

    window = max(1, rotation_hours * 3600)
    slot = int(datetime.utcnow().timestamp() // window) % len(places)
    return places[slot]


async def get_main_keyboard_for_user(chat_id: int) -> InlineKeyboardMarkup:
    """Main keyboard with optional sponsored row (up to SPONSORED_ROW_DAILY_LIMIT/day per user)."""
    base_rows = [list(row) for row in get_main_keyboard().inline_keyboard]

    try:
        if not await _is_business_offers_ui_visible():
            return InlineKeyboardMarkup(inline_keyboard=base_rows)

        if not await _is_sponsored_offers_enabled(chat_id):
            return InlineKeyboardMarkup(inline_keyboard=base_rows)

        place = await _pick_sponsored_partner_place()
        if not place:
            return InlineKeyboardMarkup(inline_keyboard=base_rows)

        today = _today_utc_str()
        if await _get_sponsored_seen_today(chat_id, today=today) >= SPONSORED_ROW_DAILY_LIMIT:
            return InlineKeyboardMarkup(inline_keyboard=base_rows)

        place_id = int(place.get("id") or 0)
        if place_id <= 0:
            return InlineKeyboardMarkup(inline_keyboard=base_rows)
        place_name = _truncate_sponsored_place_name(str(place.get("name") or "Заклад"))
        sponsored_row = [InlineKeyboardButton(text=f"⭐ Партнер: {place_name}", callback_data=f"place_{place_id}")]
        # Place sponsored row near catalog actions.
        insert_idx = 3 if len(base_rows) >= 3 else len(base_rows)
        base_rows.insert(insert_idx, sponsored_row)
        await _mark_sponsored_seen(chat_id, today=today)
    except Exception:
        logger.exception("Failed to build sponsored main-menu row for chat_id=%s", chat_id)

    return InlineKeyboardMarkup(inline_keyboard=base_rows)


def get_service_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура сервісної служби з телефонами."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏢 Адміністрація", callback_data="service_administration"),
        ],
        [
            InlineKeyboardButton(text="🧾 Бухгалтерія", callback_data="service_accounting"),
        ],
        [
            InlineKeyboardButton(text="🛡️ Охорона (цілодобово)", callback_data="service_security"),
        ],
        [
            InlineKeyboardButton(text="🔧 Сантехнік (цілодобово)", callback_data="service_plumber"),
        ],
        [
            InlineKeyboardButton(text="⚡ Електрик (цілодобово)", callback_data="service_electrician"),
        ],
        [
            InlineKeyboardButton(text="💻 ІТ відділ", callback_data="service_it"),
        ],
        [
            InlineKeyboardButton(text="🛗 Диспетчер ліфтів (цілодобово)", callback_data="service_elevator"),
        ],
        [
            InlineKeyboardButton(text="🚗 Оформлення перепустки авто", callback_data="service_car_pass"),
        ],
        [
            InlineKeyboardButton(text="🅿️ Оренда паркінгу", callback_data="service_parking"),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])


def get_quiet_keyboard(back_callback: str = "notifications_menu") -> InlineKeyboardMarkup:
    """Клавіатура для налаштування тихих годин."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌙 23:00 - 07:00", callback_data="quiet_23_7"),
            InlineKeyboardButton(text="🌙 22:00 - 08:00", callback_data="quiet_22_8"),
        ],
        [
            InlineKeyboardButton(text="🌙 00:00 - 06:00", callback_data="quiet_0_6"),
            InlineKeyboardButton(text="🔔 Вимкнути", callback_data="quiet_off"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data=back_callback),
        ],
    ])


async def get_notifications_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Клавіатура для меню сповіщень з поточними налаштуваннями."""
    settings = await get_notification_settings(chat_id)
    business_offers_visible = await _is_business_offers_ui_visible()
    sponsored_enabled = await _is_sponsored_offers_enabled(chat_id) if business_offers_visible else False
    offers_digest_enabled = await _is_offers_digest_enabled(chat_id) if business_offers_visible else False
    
    light_status = "✅" if settings["light_notifications"] else "❌"
    alert_status = "✅" if settings["alert_notifications"] else "❌"
    schedule_status = "✅" if settings["schedule_notifications"] else "❌"
    sponsored_status = "✅" if sponsored_enabled else "❌"
    digest_status = "✅" if offers_digest_enabled else "❌"
    
    # Формуємо текст для тихих годин
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        quiet_text = f"🌙 {settings['quiet_start']:02d}:00-{settings['quiet_end']:02d}:00"
    else:
        quiet_text = "🔔 Вимкнено"
    
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"☀️ Світло: {light_status}",
                callback_data="notif_toggle_light"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"🚨 Тривоги: {alert_status}",
                callback_data="notif_toggle_alert"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"📅 Графіки відключень: {schedule_status}",
                callback_data="notif_toggle_schedule"
            ),
        ],
    ]

    if business_offers_visible:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⭐ Пропозиції партнерів: {sponsored_status}",
                    callback_data="notif_toggle_sponsored"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📬 Акції тижня: {digest_status}",
                    callback_data="notif_toggle_offers_digest"
                ),
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=f"⏰ Тихі години: {quiet_text}",
                    callback_data="notif_quiet_hours"
                ),
            ],
            [
                InlineKeyboardButton(text="« Меню", callback_data="menu"),
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_buildings_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура для вибору будинку."""
    from database import BUILDINGS
    
    buttons = []
    for b in BUILDINGS:
        display_name = f"{b['name']} ({b['address']})"
        buttons.append([
            InlineKeyboardButton(
                text=display_name,
                callback_data=f"building_{b['id']}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_sections_keyboard(building_id: int, current_section: int | None = None) -> InlineKeyboardMarkup:
    """Клавіатура для вибору секції (1..N) для конкретного будинку."""
    from database import get_building_section_ids

    rows = []
    for section_id in get_building_section_ids(building_id):
        label = f"{section_id} секція"
        if current_section == section_id:
            label = f"✅ {label}"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"section_{building_id}_{section_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="« Будинки", callback_data="select_building")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "🏠 Обрати будинок")
async def reply_select_building(message: Message):
    """Обробник кнопки 'Обрати будинок' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Обрати будинок")
    try:
        await message.delete()
    except Exception:
        pass
    
    from database import get_subscriber_building_and_section
    
    building_id, section_id = await get_subscriber_building_and_section(message.chat.id)
    current_text = ""
    if building_id:
        building = get_building_by_id(building_id)
        if building:
            if section_id:
                current_text = (
                    f"\n\n📍 Ваш поточний вибір: <b>{building['name']} ({building['address']}), секція {section_id}</b>"
                )
            else:
                current_text = f"\n\n📍 Ваш поточний будинок: <b>{building['name']} ({building['address']})</b>"
    
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=(
            f"🏠 <b>Оберіть свій будинок</b>{current_text}\n\n"
            "Обравши будинок, ви будете отримувати сповіщення про світло саме по вашому будинку:"
        ),
        reply_markup=get_buildings_keyboard(),
        context_key="building_select",
    )


@router.callback_query(F.data == "select_building")
async def cb_select_building(callback: CallbackQuery):
    """Показати меню вибору будинку."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Обрати будинок")
    from database import get_subscriber_building_and_section
    
    building_id, section_id = await get_subscriber_building_and_section(callback.message.chat.id)
    current_text = ""
    if building_id:
        building = get_building_by_id(building_id)
        if building:
            if section_id:
                current_text = (
                    f"\n\n📍 Ваш поточний вибір: <b>{building['name']} ({building['address']}), секція {section_id}</b>"
                )
            else:
                current_text = f"\n\n📍 Ваш поточний будинок: <b>{building['name']} ({building['address']})</b>"
    
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=(
            f"🏠 <b>Оберіть свій будинок</b>{current_text}\n\n"
            "Обравши будинок, ви будете отримувати сповіщення про світло саме по вашому будинку:"
        ),
        reply_markup=get_buildings_keyboard(),
        context_key="building_select",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("building_"))
async def cb_building_selected(callback: CallbackQuery):
    """Обробка вибору будинку."""
    from database import (
        set_subscriber_building,
        set_subscriber_section,
        get_subscriber_section,
        get_building_by_id,
        add_subscriber,
        default_section_for_building,
    )
    
    building_id = int(callback.data.split("_")[1])
    building = get_building_by_id(building_id)
    
    if not building:
        await safe_callback_answer(callback, "❌ Будинок не знайдено", show_alert=True)
        return
    
    # Спочатку переконаємось що користувач є в базі
    user = callback.from_user
    await add_subscriber(
        chat_id=callback.message.chat.id,
        username=user.username if user else None,
        first_name=user.first_name if user else None
    )
    
    # Встановлюємо будинок
    await set_subscriber_building(callback.message.chat.id, building_id)
    # Якщо секція ще не обрана — підкажемо дефолт (але все одно дамо вибір)
    current_section = await get_subscriber_section(callback.message.chat.id)
    if current_section is None:
        await set_subscriber_section(callback.message.chat.id, default_section_for_building(building_id))
        current_section = await get_subscriber_section(callback.message.chat.id)
    
    display_name = f"{building['name']} ({building['address']})"

    text = (
        f"🏠 <b>Будинок: {display_name}</b>\n\n"
        "Тепер оберіть вашу секцію, щоб отримувати точні сповіщення саме по ній:"
    )
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=get_sections_keyboard(building_id, current_section=current_section),
        context_key="building_select",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("section_"))
async def cb_section_selected(callback: CallbackQuery):
    """Обробка вибору секції."""
    from database import (
        set_subscriber_building,
        set_subscriber_section,
        get_building_by_id,
        add_subscriber,
        is_valid_section_for_building,
    )

    try:
        _, building_id_raw, section_id_raw = callback.data.split("_", 2)
        building_id = int(building_id_raw)
        section_id = int(section_id_raw)
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректні дані секції", show_alert=True)
        return

    building = get_building_by_id(building_id)
    if not building:
        await safe_callback_answer(callback, "❌ Будинок не знайдено", show_alert=True)
        return
    if not is_valid_section_for_building(building_id, section_id):
        await safe_callback_answer(callback, "❌ Некоректна секція", show_alert=True)
        return

    user = callback.from_user
    await add_subscriber(
        chat_id=callback.message.chat.id,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
    )

    await set_subscriber_building(callback.message.chat.id, building_id)
    await set_subscriber_section(callback.message.chat.id, section_id)

    display_name = f"{building['name']} ({building['address']})"
    text = (
        f"✅ <b>Збережено</b>\n\n"
        f"🏠 {display_name}\n"
        f"🔢 Секція: <b>{section_id}</b>\n\n"
        "Тепер ви будете отримувати сповіщення про світло по вашій секції."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☀️ Перевірити світло", callback_data="status")],
        [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
    ])
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="building_select",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Підписати чат на сповіщення або обробити deep link."""
    try:
        await message.delete()
    except Exception:
        pass
    user = message.from_user
    await add_subscriber(
        chat_id=message.chat.id,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
    )
    logger.info(f"User {format_user_label(user, message.chat.id)} started bot")
    
    # Перевіряємо чи є deep link параметр (place_123)
    args = message.text.split()[1] if len(message.text.split()) > 1 else None
    
    if args and args.startswith("place_"):
        # Deep link для перегляду закладу з картою
        try:
            place_id = int(args.replace("place_", ""))
            await show_place_with_map(message, place_id)
            return
        except (ValueError, Exception):
            pass
    
    # Також показуємо InlineKeyboard в чаті
    building_text = await get_user_building_text(message.chat.id)
    light_status = await get_light_status_text(message.chat.id)
    alert_status = await get_alert_status_text()
    await remove_reply_keyboard(message)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=await get_main_keyboard_for_user(message.chat.id),
        context_key="main_menu",
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    """Показати головне меню з кнопками."""
    logger.info(f"User {message.chat.id} opened menu")
    # Показуємо InlineKeyboard
    building_text = await get_user_building_text(message.chat.id)
    light_status = await get_light_status_text(message.chat.id)
    alert_status = await get_alert_status_text()
    await remove_reply_keyboard(message)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=f"{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=await get_main_keyboard_for_user(message.chat.id),
        context_key="main_menu",
    )


@router.message(Command("unsubscribe"))
async def cmd_unsub(message: Message):
    """Відписати чат від сповіщень."""
    await remove_subscriber(message.chat.id)
    await message.answer("Ок. Відписав цей чат.")


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Показати поточний статус світла."""
    text = await format_light_status(message.chat.id, include_vote_prompt=False)
    await message.answer(text)


@router.message(Command("quiet"))
async def cmd_quiet(message: Message):
    """
    Налаштувати тихі години.
    Формат: /quiet 23 7 — не турбувати з 23:00 до 7:00
    /quiet off — вимкнути тихі години
    /quiet — показати поточні налаштування
    """
    chat_id = message.chat.id
    args = message.text.split()[1:] if message.text else []
    
    if not args:
        # Показати поточні налаштування
        start, end = await get_quiet_hours(chat_id)
        if start is None or end is None:
            await message.answer(
                "🔔 Тихі години не налаштовані.\n\n"
                "Щоб налаштувати:\n"
                "<code>/quiet 23 7</code> — не турбувати з 23:00 до 7:00\n"
                "<code>/quiet off</code> — вимкнути"
            )
        else:
            await message.answer(
                f"🌙 Тихі години: з {start:02d}:00 до {end:02d}:00\n\n"
                "<code>/quiet off</code> — вимкнути"
            )
        return
    
    if args[0].lower() == "off":
        await set_quiet_hours(chat_id, None, None)
        await message.answer("🔔 Тихі години вимкнено. Сповіщення будуть надходити цілодобово.")
        return
    
    if len(args) < 2:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Приклад: <code>/quiet 23 7</code> — не турбувати з 23:00 до 7:00"
        )
        return
    
    try:
        start = int(args[0])
        end = int(args[1])
        
        if not (0 <= start <= 23 and 0 <= end <= 23):
            raise ValueError("Години мають бути від 0 до 23")
        
        await set_quiet_hours(chat_id, start, end)
        await message.answer(
            f"🌙 Готово! Тихі години: з {start:02d}:00 до {end:02d}:00\n"
            "У цей час сповіщення не надходитимуть."
        )
    except ValueError:
        await message.answer(
            "❌ Невірний формат годин.\n\n"
            "Приклад: <code>/quiet 23 7</code> — не турбувати з 23:00 до 7:00"
        )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """
    Показати статистику відключень.
    /stats — загальна статистика
    /stats day — за останню добу
    /stats week — за останній тиждень
    /stats month — за останній місяць
    """
    args = message.text.split()[1:] if message.text else []
    
    period_map = {
        "day": (1, "за останню добу"),
        "день": (1, "за останню добу"),
        "week": (7, "за останній тиждень"),
        "тиждень": (7, "за останній тиждень"),
        "month": (30, "за останній місяць"),
        "місяць": (30, "за останній місяць"),
    }
    
    if args and args[0].lower() in period_map:
        days, period_text = period_map[args[0].lower()]
    else:
        days = None
        period_text = "за весь час"

    text = await format_stats_message_for_user(message.chat.id, days, period_text)
    await message.answer(text)


# ============ Callback handlers (Inline-кнопки) ============

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    """Показати головне меню."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Головне меню")
    building_text = await get_user_building_text(callback.from_user.id)
    light_status = await get_light_status_text(callback.message.chat.id)
    alert_status = await get_alert_status_text()
    text = f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:"
    main_keyboard = await get_main_keyboard_for_user(callback.message.chat.id)
    
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=main_keyboard,
        context_key="main_menu",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
        force_new_message=bool(getattr(callback.message, "photo", None)),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "utilities_menu")
async def cb_utilities_menu(callback: CallbackQuery):
    """Показати меню Світло/Опалення/Вода."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Світло/опалення/вода")
    text = "💡 <b>Світло / Опалення / Вода</b>\n\nОберіть розділ:"
    buttons = [
        [InlineKeyboardButton(text="☀️ Світло", callback_data="status")],
        [InlineKeyboardButton(text="♨️ Опалення", callback_data="heating_menu")],
        [InlineKeyboardButton(text="💧 Вода", callback_data="water_menu")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="« Меню", callback_data="menu")],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="utilities_menu",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "alerts_menu")
async def cb_alerts_menu(callback: CallbackQuery):
    """Показати меню Тривоги та укриття."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Тривоги та укриття")
    alert_status = await get_alert_status_text()
    text = f"🚨 <b>Тривоги та укриття</b>\n\nПоточний стан: {alert_status}\n\nОберіть дію:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📡 Стан тривоги", callback_data="alert_status"),
        ],
        [
            InlineKeyboardButton(text="🏛 Укриття", callback_data="shelters"),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="alerts_menu",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "alert_status")
async def cb_alert_status(callback: CallbackQuery):
    """Показати поточний стан тривоги (з кешу БД)."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Стан тривоги")
    alert_state = await db_get("last_alert_state")
    
    if alert_state == "active":
        text = (
            "🚨 <b>ПОВІТРЯНА ТРИВОГА!</b>\n\n"
            "⚠️ Оголошено повітряну тривогу в місті Київ.\n"
            "🏃 Прямуйте до найближчого укриття!"
        )
    elif alert_state == "inactive":
        text = (
            "✅ <b>Відбій тривоги</b>\n\n"
            "Наразі повітряної тривоги в Києві немає.\n"
            "🏠 Можна залишатись вдома."
        )
    else:
        text = "❓ <b>Статус невідомий</b>\n\nДані про тривогу ще не отримано."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Оновити", callback_data="alert_status"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data="alerts_menu"),
        ],
    ])
    await render_or_edit(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="alerts_menu",
        prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "shelters")
async def cb_shelters(callback: CallbackQuery):
    """Показати інформацію про укриття."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Укриття")
    from database import get_shelter_places_with_likes
    
    text = (
        "🏛 <b>Укриття</b>\n\n"
        "В ЖК «Нова Англія» наразі відсутні офіційні укриття.\n"
        "Втім, є відносно безпечні місця на випадок тривоги:\n"
        "підземний паркінг та комора для мешканців Кембріджа.\n\n"
        "Оберіть місце, щоб переглянути деталі:"
    )
    shelters = await get_shelter_places_with_likes()
    buttons = []
    if shelters:
        for shelter in shelters:
            likes_text = f" ❤️{shelter['likes_count']}" if shelter["likes_count"] > 0 else ""
            buttons.append([
                InlineKeyboardButton(
                    text=f"{shelter['name']}{likes_text}",
                    callback_data=f"shelter_{shelter['id']}"
                )
            ])
    else:
        text += "\n\n❗️ Дані про укриття ще не заповнені."
    
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="alerts_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await render_or_edit(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=keyboard,
            context_key="shelters_menu",
            force_new_message=True,
        )
    else:
        await render_or_edit(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=keyboard,
            context_key="shelters_menu",
            prefer_message_id=int(getattr(callback.message, "message_id", 0) or 0),
        )
    await safe_callback_answer(callback)


@router.callback_query(
    F.data.startswith("shelter_")
    & ~F.data.startswith("shelter_like_")
    & ~F.data.startswith("shelter_unlike_")
)
async def cb_shelter_detail(callback: CallbackQuery):
    """Показати деталі укриття."""
    from database import get_shelter_place, has_liked_shelter, get_shelter_likes_count
    
    shelter_id = int(callback.data.split("_")[1])
    shelter = await get_shelter_place(shelter_id)
    
    if not shelter:
        await safe_callback_answer(callback, "Укриття не знайдено", show_alert=True)
        return
    
    user_liked = await has_liked_shelter(shelter_id, callback.from_user.id)
    likes_count = await get_shelter_likes_count(shelter_id)
    admin_tag = CFG.admin_tag or "адміністратору"
    
    text = f"🏛 <b>{shelter['name']}</b>\n\n"
    if shelter["description"]:
        text += f"📝 {shelter['description']}\n\n"
    if shelter["address"]:
        text += f"📍 <b>Локація:</b> {shelter['address']}\n\n"
    text += f"❤️ <b>Лайків:</b> {likes_count}\n\n"
    text += f"💬 Побачили помилку? Пишіть {admin_tag}"
    
    map_file = get_map_file_for_address(shelter["address"])
    
    if user_liked:
        like_btn = InlineKeyboardButton(
            text=f"💔 Забрати лайк ({likes_count})",
            callback_data=f"shelter_unlike_{shelter_id}"
        )
    else:
        like_btn = InlineKeyboardButton(
            text=f"❤️ Подобається ({likes_count})",
            callback_data=f"shelter_like_{shelter_id}"
        )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [like_btn],
        [InlineKeyboardButton(text="« Назад", callback_data="shelters")],
    ])
    
    if map_file:
        try:
            await callback.message.delete()
        except Exception:
            pass
        photo = FSInputFile(map_file)
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=keyboard
        )
    else:
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("shelter_like_"))
async def cb_like_shelter(callback: CallbackQuery):
    """Поставити лайк укриттю."""
    from database import like_shelter, get_shelter_likes_count
    
    shelter_id = int(callback.data.split("_")[2])
    added = await like_shelter(shelter_id, callback.from_user.id)
    
    if added:
        likes_count = await get_shelter_likes_count(shelter_id)
        await safe_callback_answer(callback, f"❤️ Дякуємо за лайк! Усього: {likes_count}")
    else:
        await safe_callback_answer(callback, "Ви вже лайкнули це укриття")
    
    likes_count = await get_shelter_likes_count(shelter_id)
    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💔 Забрати лайк ({likes_count})", callback_data=f"shelter_unlike_{shelter_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="shelters")],
    ])
    
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=callback.message.caption,
                reply_markup=new_keyboard
            )
        else:
            await callback.message.edit_reply_markup(reply_markup=new_keyboard)
    except Exception:
        pass


@router.callback_query(F.data.startswith("shelter_unlike_"))
async def cb_unlike_shelter(callback: CallbackQuery):
    """Забрати лайк із укриття."""
    from database import unlike_shelter, get_shelter_likes_count
    
    shelter_id = int(callback.data.split("_")[2])
    removed = await unlike_shelter(shelter_id, callback.from_user.id)
    
    if removed:
        likes_count = await get_shelter_likes_count(shelter_id)
        await safe_callback_answer(callback, f"💔 Лайк забрано. Усього: {likes_count}")
    else:
        await safe_callback_answer(callback, "Ви не лайкали це укриття")
    
    likes_count = await get_shelter_likes_count(shelter_id)
    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❤️ Подобається ({likes_count})", callback_data=f"shelter_like_{shelter_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="shelters")],
    ])
    
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=callback.message.caption,
                reply_markup=new_keyboard
            )
        else:
            await callback.message.edit_reply_markup(reply_markup=new_keyboard)
    except Exception:
        pass


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery):
    """Показати поточний статус світла."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Світло")
    text = await format_light_status(callback.message.chat.id, include_vote_prompt=False)

    buttons = [
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="status")],
        [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
    ]

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await safe_callback_answer(callback)


async def format_stats_message_for_user(
    user_id: int,
    days: int | None,
    period_text: str,
) -> str:
    """Форматувати повідомлення зі статистикою по обраній секції користувача."""
    from database import get_subscriber_building_and_section, get_building_by_id, is_valid_section_for_building

    building_id, section_id = await get_subscriber_building_and_section(user_id)
    building = get_building_by_id(building_id) if building_id else None

    if not building:
        return (
            "📊 <b>Статистика</b>\n\n"
            "⚠️ Спочатку оберіть будинок і секцію.\n"
            "Натисніть «🏠 Обрати будинок»."
        )
    if not is_valid_section_for_building(building_id, section_id):
        return (
            "📊 <b>Статистика</b>\n\n"
            f"🏠 {building['name']} ({building['address']})\n\n"
            "⚠️ Спочатку оберіть секцію.\n"
            "Натисніть «🏠 Обрати будинок» і оберіть секцію."
        )

    stats = await calculate_stats(days, building_id=building_id, section_id=section_id)

    if stats["outage_count"] == 0:
        return (
            f"📊 <b>Статистика {period_text}</b>\n\n"
            f"🏠 {building['name']} ({building['address']}), секція {section_id}\n\n"
            "✨ Відключень не зафіксовано!\n"
            "⚡ Uptime: 100%"
        )

    response = (
        f"📊 <b>Статистика {period_text}</b>\n\n"
        f"🏠 {building['name']} ({building['address']}), секція {section_id}\n\n"
        f"⚡ Uptime: {stats['uptime_percent']:.1f}%\n"
        f"🔌 Кількість відключень: {stats['outage_count']}\n"
        f"⏱ Загальний час без світла: {format_duration(stats['total_downtime'])}\n"
    )

    if stats["outage_count"] > 0:
        avg_outage = stats["total_downtime"] / stats["outage_count"]
        response += f"📉 Середня тривалість: {format_duration(avg_outage)}\n"

    response += (
        f"\n<i>Період: {stats['period_start'].strftime('%d.%m.%Y %H:%M')} — "
        f"{stats['period_end'].strftime('%d.%m.%Y %H:%M')}</i>"
    )

    return response


@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):
    """Показати статистику за весь час."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (весь час)")
    text = await format_stats_message_for_user(callback.message.chat.id, None, "за весь час")
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 День", callback_data="stats_day"),
                InlineKeyboardButton(text="📆 Тиждень", callback_data="stats_week"),
                InlineKeyboardButton(text="🗓 Місяць", callback_data="stats_month"),
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "stats_day")
async def cb_stats_day(callback: CallbackQuery):
    """Показати статистику за день."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (день)")
    text = await format_stats_message_for_user(callback.message.chat.id, 1, "за останню добу")
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📆 Тиждень", callback_data="stats_week"),
                InlineKeyboardButton(text="🗓 Місяць", callback_data="stats_month"),
                InlineKeyboardButton(text="🗓 Весь час", callback_data="stats"),
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "stats_week")
async def cb_stats_week(callback: CallbackQuery):
    """Показати статистику за тиждень."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (тиждень)")
    text = await format_stats_message_for_user(callback.message.chat.id, 7, "за останній тиждень")
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 День", callback_data="stats_day"),
                InlineKeyboardButton(text="🗓 Місяць", callback_data="stats_month"),
                InlineKeyboardButton(text="🗓 Весь час", callback_data="stats"),
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "stats_month")
async def cb_stats_month(callback: CallbackQuery):
    """Показати статистику за місяць."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (місяць)")
    text = await format_stats_message_for_user(callback.message.chat.id, 30, "за останній місяць")
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 День", callback_data="stats_day"),
                InlineKeyboardButton(text="📆 Тиждень", callback_data="stats_week"),
                InlineKeyboardButton(text="🗓 Весь час", callback_data="stats"),
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ])
    )
    await safe_callback_answer(callback)


# ============ Меню Сповіщень ============

@router.callback_query(F.data == "notifications_menu")
async def cb_notifications_menu(callback: CallbackQuery):
    """Показати меню налаштувань сповіщень."""
    chat_id = callback.message.chat.id
    settings = await get_notification_settings(chat_id)
    
    text = (
        "🔔 <b>Сповіщення</b>\n\n"
        "Тут ви можете налаштувати які сповіщення отримувати:\n\n"
        f"☀️ <b>Світло:</b> {'увімкнено ✅' if settings['light_notifications'] else 'вимкнено ❌'}\n"
        f"🚨 <b>Тривоги:</b> {'увімкнено ✅' if settings['alert_notifications'] else 'вимкнено ❌'}\n"
        f"📅 <b>Графіки відключень:</b> {'увімкнено ✅' if settings['schedule_notifications'] else 'вимкнено ❌'}\n"
    )
    
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        text += f"\n⏰ <b>Тихі години:</b> {settings['quiet_start']:02d}:00 - {settings['quiet_end']:02d}:00"
    else:
        text += "\n⏰ <b>Тихі години:</b> вимкнено"
    
    await callback.message.edit_text(
        text,
        reply_markup=await get_notifications_keyboard(chat_id)
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "notif_toggle_light")
async def cb_toggle_light_notifications(callback: CallbackQuery):
    """Переключити сповіщення про світло."""
    chat_id = callback.message.chat.id
    settings = await get_notification_settings(chat_id)
    
    new_value = not settings["light_notifications"]
    await set_light_notifications(chat_id, new_value)
    
    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await safe_callback_answer(callback, f"☀️ Сповіщення про світло {status}")
    
    # Оновлюємо меню
    await cb_notifications_menu(callback)


@router.callback_query(F.data == "notif_toggle_alert")
async def cb_toggle_alert_notifications(callback: CallbackQuery):
    """Переключити сповіщення про тривоги."""
    chat_id = callback.message.chat.id
    settings = await get_notification_settings(chat_id)
    
    new_value = not settings["alert_notifications"]
    await set_alert_notifications(chat_id, new_value)
    
    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await safe_callback_answer(callback, f"🚨 Сповіщення про тривоги {status}")
    
    # Оновлюємо меню
    await cb_notifications_menu(callback)


@router.callback_query(F.data == "notif_toggle_schedule")
async def cb_toggle_schedule_notifications(callback: CallbackQuery):
    """Переключити сповіщення про графіки ЯСНО."""
    chat_id = callback.message.chat.id
    settings = await get_notification_settings(chat_id)

    new_value = not settings["schedule_notifications"]
    await set_schedule_notifications(chat_id, new_value)

    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await safe_callback_answer(callback, f"📅 Сповіщення про графіки {status}")

    await cb_notifications_menu(callback)


@router.callback_query(F.data == "notif_toggle_sponsored")
async def cb_toggle_sponsored_offers(callback: CallbackQuery):
    """Переключити показ спонсорованих пропозицій у головному меню."""
    if not await _is_business_offers_ui_visible():
        await safe_callback_answer(
            callback,
            "Опція зʼявиться після появи першого Verified закладу.",
            show_alert=True,
        )
        await cb_notifications_menu(callback)
        return

    chat_id = callback.message.chat.id
    enabled = await _is_sponsored_offers_enabled(chat_id)
    new_value = not enabled
    await _set_sponsored_offers_enabled(chat_id, new_value)

    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await safe_callback_answer(callback, f"⭐ Спонсоровані пропозиції {status}")

    await cb_notifications_menu(callback)


@router.callback_query(F.data == "notif_toggle_offers_digest")
async def cb_toggle_offers_digest(callback: CallbackQuery):
    """Переключити підписку на щотижневий дайджест акцій партнерів."""
    if not await _is_business_offers_ui_visible():
        await safe_callback_answer(
            callback,
            "Опція зʼявиться після появи першого Verified закладу.",
            show_alert=True,
        )
        await cb_notifications_menu(callback)
        return

    chat_id = callback.message.chat.id
    enabled = await _is_offers_digest_enabled(chat_id)
    new_value = not enabled
    await _set_offers_digest_enabled(chat_id, new_value)

    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await safe_callback_answer(callback, f"📬 Акції тижня {status}")
    await cb_notifications_menu(callback)


@router.callback_query(F.data == "notif_quiet_hours")
async def cb_quiet_hours_menu(callback: CallbackQuery):
    """Показати меню налаштування тихих годин."""
    chat_id = callback.message.chat.id
    start, end = await get_quiet_hours(chat_id)
    
    if start is None or end is None:
        text = (
            "⏰ <b>Тихі години</b>\n\n"
            "У тихі години сповіщення не надходитимуть.\n"
            "Оберіть зручний варіант:"
        )
    else:
        text = (
            f"⏰ <b>Тихі години</b>\n\n"
            f"Зараз: з {start:02d}:00 до {end:02d}:00\n"
            "У цей час сповіщення не надходитимуть.\n\n"
            "Змінити:"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_quiet_keyboard("notifications_menu")
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "quiet_info")
async def cb_quiet_info(callback: CallbackQuery):
    """Показати інформацію про тихі години (редирект на нове меню)."""
    await cb_notifications_menu(callback)

@router.callback_query(F.data.startswith("quiet_"))
async def cb_quiet_set(callback: CallbackQuery):
    """Встановити тихі години."""
    chat_id = callback.message.chat.id
    data = callback.data
    
    if data == "quiet_off":
        await set_quiet_hours(chat_id, None, None)
        await safe_callback_answer(callback, "🔔 Тихі години вимкнено")
    else:
        # Парсимо quiet_23_7 -> start=23, end=7
        parts = data.replace("quiet_", "").split("_")
        if len(parts) == 2:
            start, end = int(parts[0]), int(parts[1])
            await set_quiet_hours(chat_id, start, end)
            await safe_callback_answer(callback, f"🌙 Тихі години: {start:02d}:00 - {end:02d}:00")
        else:
            await safe_callback_answer(callback, "Помилка")
            return
    
    # Повертаємось до меню сповіщень
    await cb_notifications_menu(callback)


## Admin commands moved to a separate admin-bot (control-plane).
## Main user-bot must remain free of admin-side controls.


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    """Показати свій Telegram ID."""
    await message.answer(
        f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>\n\n"
        f"Додайте цей ID в ADMIN_IDS для отримання прав адміністратора."
    )


# ============ Обробники текстових повідомлень від ReplyKeyboard ============

@router.message(F.text == "💡 Світло/опалення/вода")
async def reply_utilities(message: Message):
    """Обробник кнопки 'Світло/опалення/вода' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Світло/опалення/вода")
    try:
        await message.delete()
    except Exception:
        pass
    
    text = "💡 <b>Світло / Опалення / Вода</b>\n\nОберіть розділ:"
    buttons = [
        [InlineKeyboardButton(text="☀️ Світло", callback_data="status")],
        [InlineKeyboardButton(text="♨️ Опалення", callback_data="heating_menu")],
        [InlineKeyboardButton(text="💧 Вода", callback_data="water_menu")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="« Меню", callback_data="menu")],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="main_menu",
    )


@router.message(F.text == "🚨 Тривоги та укриття")
async def reply_alerts(message: Message):
    """Обробник кнопки 'Тривоги та укриття' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Тривоги та укриття")
    try:
        await message.delete()
    except Exception:
        pass
    
    alert_status = await get_alert_status_text()
    text = f"🚨 <b>Тривоги та укриття</b>\n\nПоточний стан: {alert_status}\n\nОберіть дію:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📡 Стан тривоги", callback_data="alert_status"),
        ],
        [
            InlineKeyboardButton(text="🏛 Укриття", callback_data="shelters"),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=keyboard,
        context_key="main_menu",
    )


@router.message(F.text == "🔔 Сповіщення та тихі години")
async def reply_notifications(message: Message):
    """Обробник кнопки 'Сповіщення та тихі години' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Сповіщення та тихі години")
    try:
        await message.delete()
    except Exception:
        pass
    
    chat_id = message.chat.id
    settings = await get_notification_settings(chat_id)
    
    text = (
        "🔔 <b>Сповіщення та тихі години</b>\n\n"
        "Тут ви можете налаштувати які сповіщення отримувати:\n\n"
        f"☀️ <b>Світло:</b> {'увімкнено ✅' if settings['light_notifications'] else 'вимкнено ❌'}\n"
        f"🚨 <b>Тривоги:</b> {'увімкнено ✅' if settings['alert_notifications'] else 'вимкнено ❌'}\n"
        f"📅 <b>Графіки відключень:</b> {'увімкнено ✅' if settings['schedule_notifications'] else 'вимкнено ❌'}\n"
    )
    
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        text += f"\n⏰ <b>Тихі години:</b> {settings['quiet_start']:02d}:00 - {settings['quiet_end']:02d}:00"
    else:
        text += "\n⏰ <b>Тихі години:</b> вимкнено"
    
    await render_or_edit(
        bot=message.bot,
        chat_id=chat_id,
        text=text,
        reply_markup=await get_notifications_keyboard(chat_id),
        context_key="main_menu",
    )


@router.message(F.text == "🌙 Тихі години")
async def reply_quiet(message: Message):
    """Обробник кнопки 'Тихі години' з ReplyKeyboard (для сумісності)."""
    if await handle_webapp_reply_keyboard(message):
        return
    await reply_notifications(message)


# ============ Обробники для СТАРИХ кнопок (сумісність з попередньою версією) ============
# У режимі WebApp ці кнопки прибирають кешовану ReplyKeyboard і показують актуальне меню.
LEGACY_REPLY_TEXTS = {
    "🏠 Обрати будинок",
    "💡 Світло/опалення/вода",
    "🏢 Заклади в ЖК",
    "🔍 Пошук закладу",
    "🚨 Тривоги та укриття",
    "📞 Сервісна служба",
    "🔔 Сповіщення та тихі години",
    "🎁 РОЗІГРАШ",
    "💡 Світло",
    "☀️ Світло",
    "♨️ Опалення",
    "🔥 Опалення",
    "💧 Вода",
    "🔔 Сповіщення",
    "📈 Статистика",
    "🔍 Пошук",
    "🌙 Тихі години",
    "Головне меню",
    "Меню",
    # старі варіанти без емодзі (дуже стара ReplyKeyboard)
    "Світло",
    "Опалення",
    "Вода",
    "Заклади в ЖК",
    "Пошук",
    "Сервісна служба",
    "Статистика",
    "Сповіщення",
    "Тривоги та укриття",
}

# Додатковий regex для ловлі старих кнопок з емоджі/зайвими символами
LEGACY_REPLY_REGEX = re.compile(
    r"^\s*[^A-Za-zА-Яа-яІіЇїЄєҐґ0-9]*\s*("
    r"Головне меню|Меню|Обрати будинок|Світло/опалення/вода|Світло|Опалення|Вода|"
    r"Заклади в ЖК|Пошук закладу|Пошук|Сервісна служба|Статистика|"
    r"Сповіщення та тихі години|Сповіщення|Тривоги та укриття|Розіграш"
    r")\s*$",
    re.IGNORECASE,
)

@router.message(F.text == "💡 Світло")
async def reply_light_old(message: Message):
    """Обробник СТАРОЇ кнопки 'Світло'."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} uses old button: Світло - updating keyboard")
    try:
        await message.delete()
    except Exception:
        pass
    await remove_reply_keyboard(message)
    # Викликаємо нову функціональність - показуємо статус світла
    text = await format_light_status(message.chat.id, include_vote_prompt=False)
    buttons = [[InlineKeyboardButton(text="🔄 Оновити", callback_data="status")]]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")])
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        context_key="main_menu",
    )


@router.message(F.text == "♨️ Опалення")
async def reply_heating_old(message: Message):
    """Обробник СТАРОЇ кнопки 'Опалення'."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} uses old button: Опалення - updating keyboard")
    try:
        await message.delete()
    except Exception:
        pass
    await remove_reply_keyboard(message)
    # Викликаємо нову функціональність
    from database import get_user_vote
    user_vote = await get_user_vote(message.chat.id, "heating")
    text = await format_heating_status(message.chat.id)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_heating_vote_keyboard(user_vote),
        context_key="main_menu",
    )


@router.message(F.text == "💧 Вода")
async def reply_water_old(message: Message):
    """Обробник СТАРОЇ кнопки 'Вода'."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} uses old button: Вода - updating keyboard")
    try:
        await message.delete()
    except Exception:
        pass
    await remove_reply_keyboard(message)
    # Викликаємо нову функціональність
    from database import get_user_vote
    user_vote = await get_user_vote(message.chat.id, "water")
    text = await format_water_status(message.chat.id)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_water_vote_keyboard(user_vote),
        context_key="main_menu",
    )


@router.message(F.text == "🔔 Сповіщення")
async def reply_notifications_old(message: Message):
    """Обробник СТАРОЇ кнопки 'Сповіщення'."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} uses old button: Сповіщення - updating keyboard")
    await remove_reply_keyboard(message)
    # Перенаправляємо на нову функцію
    await reply_notifications(message)


@router.message(F.text == "🔍 Пошук")
async def reply_search_old(message: Message):
    """Обробник СТАРОЇ кнопки 'Пошук'."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} uses old button: Пошук - updating keyboard")
    try:
        await message.delete()
    except Exception:
        pass
    await remove_reply_keyboard(message)
    # Показуємо пошук
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text="🔍 <b>Пошук закладу</b>\n\nВведіть назву або ключове слово для пошуку:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Меню", callback_data="menu")]]
        ),
        context_key="search_menu",
    )


@router.message(StateFilter(None), F.text.in_(LEGACY_REPLY_TEXTS))
async def reply_keyboard_fallback(message: Message):
    """Фолбек: якщо прийшов текст з ReplyKeyboard у режимі WebApp — прибираємо клавіатуру."""
    if await handle_webapp_reply_keyboard(message):
        return


@router.message(
    StateFilter(None),
    F.text & ~F.text.startswith("/"),
    lambda message: message.chat.id not in search_waiting_users,
)
async def reply_keyboard_regex_fallback(message: Message):
    """Regex-фолбек для дуже старих або варіативних reply-кнопок."""
    if not CFG.web_app_enabled:
        return
    text = message.text or ""
    if text in LEGACY_REPLY_TEXTS:
        return
    if LEGACY_REPLY_REGEX.match(text):
        await handle_webapp_reply_keyboard(message)


@router.message(F.text == "📞 Сервісна служба")
async def reply_service(message: Message):
    """Обробник кнопки 'Сервісна служба' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Сервісна служба")
    try:
        await message.delete()
    except Exception:
        pass
    
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=(
            "📞 <b>Сервісна служба</b>\n\n"
            "🕘 Нова Англія сервіс, працює з понеділка по п'ятницю з 9:00 - 18:00, "
            "субота з 10:00 - 16:00.\n\n"
            "Оберіть службу для отримання контактного телефону:"
        ),
        reply_markup=get_service_keyboard(),
        context_key="main_menu",
    )


# ============ Callback-обробники сервісної служби ============

@router.callback_query(F.data == "service_menu")
async def cb_service_menu(callback: CallbackQuery):
    """Показати меню сервісної служби."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Сервісна служба")
    await callback.message.edit_text(
        "📞 <b>Сервісна служба</b>\n\n"
        "🕘 Нова Англія сервіс, працює з понеділка по п'ятницю з 9:00 - 18:00, "
        "субота з 10:00 - 16:00.\n\n"
        "Оберіть службу для отримання контактного телефону:",
        reply_markup=get_service_keyboard()
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_administration")
async def cb_service_administration(callback: CallbackQuery):
    """Показати контакти адміністрації."""
    await callback.message.edit_text(
        "🏢 <b>Адміністрація</b>\n\n"
        "📞 Телефони:\n"
        "• <code>067-107-38-08</code> (вайбер)\n"
        "• <code>044-300-18-77</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_accounting")
async def cb_service_accounting(callback: CallbackQuery):
    """Показати контакти бухгалтерії."""
    await callback.message.edit_text(
        "🧾 <b>Бухгалтерія</b>\n\n"
        "📞 Телефони:\n"
        "• <code>044-300-12-45</code>\n"
        "• <code>067-558-35-77</code> (вайбер)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_security")
async def cb_service_security(callback: CallbackQuery):
    """Показати телефон охорони."""
    phone = CFG.security_phone or "не вказано"
    await callback.message.edit_text(
        "🛡️ <b>Охорона (цілодобово)</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_plumber")
async def cb_service_plumber(callback: CallbackQuery):
    """Показати телефон сантехніка."""
    phone = CFG.plumber_phone or "не вказано"
    await callback.message.edit_text(
        "🔧 <b>Сантехнік (цілодобово)</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_electrician")
async def cb_service_electrician(callback: CallbackQuery):
    """Показати телефон електрика."""
    phone = CFG.electrician_phone or "не вказано"
    await callback.message.edit_text(
        "⚡ <b>Електрик (цілодобово)</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_it")
async def cb_service_it(callback: CallbackQuery):
    """Показати контакт ІТ відділу."""
    await callback.message.edit_text(
        "💻 <b>ІТ відділ</b>\n\n"
        "📞 Телефон: <code>067-599-88-15</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_elevator")
async def cb_service_elevator(callback: CallbackQuery):
    """Показати телефон диспетчера ліфтів."""
    phones = CFG.elevator_phones or "не вказано"
    # Форматуємо телефони якщо їх кілька
    phone_lines = "".join([f"• <code>{p.strip()}</code>\n" for p in phones.split(",")]) if "," in phones else f"<code>{phones}</code>"
    await callback.message.edit_text(
        "🛗 <b>Диспетчер ліфтів (цілодобово)</b>\n\n"
        f"📞 Телефони:\n{phone_lines}\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_car_pass")
async def cb_service_car_pass(callback: CallbackQuery):
    """Показати гайд з оформлення перепустки авто."""
    await callback.message.edit_text(
        "🚗 <b>Оформлення перепустки авто</b>\n\n"
        "Щоб мати можливість замовляти перепустки для кур'єрів, гостей, таксі тощо, виконайте наступні кроки:\n\n"
        "1️⃣ Напишіть @SkdNa12 в особисті повідомлення та надішліть фото договору оренди або документа, що підтверджує право власності на житло, Ваш номер телефону та документ що посвідчує Вашу особу. Вам нададуть код активації.\n\n"
        "2️⃣ Додайте бота @OhoronaSheriff_NA_bot та введіть отриманий код.\n\n"
        "3️⃣ Готово! Тепер ви можете створювати заявки на перепустки через цього бота.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "service_parking")
async def cb_service_parking(callback: CallbackQuery):
    """Показати інформацію про оренду паркінгу."""
    await callback.message.edit_text(
        "🅿️ <b>Оренда паркінгу</b>\n\n"
        "Є два способи орендувати паркомісце:\n\n"
        "📢 <b>Оголошення від мешканців</b>\n"
        "Шукайте актуальні пропозиції в телеграм-каналі:\n"
        "👉 https://t.me/newengland_parking\n\n"
        "🌐 <b>Онлайн-бронювання</b>\n"
        "Орендуйте через сервіс ParkSpot:\n"
        "👉 https://parkspot.com.ua/catalog/nova-angliya\n\n"
        "💡 <i>Обирайте місця з позначкою «авто бронювання», сплачуйте онлайн та отримуйте PIN-код для в'їзду автоматично.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ]),
        disable_web_page_preview=True
    )
    await safe_callback_answer(callback)


# ============ Меню "Заклади в ЖК" ============

async def get_places_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура з категоріями закладів."""
    from database import get_all_general_services
    
    services = await get_all_general_services()
    
    buttons = []
    for service in services:
        buttons.append([
            InlineKeyboardButton(
                text=service["name"],
                callback_data=f"places_cat_{service['id']}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "🏢 Заклади в ЖК")
async def reply_places(message: Message):
    """Обробник кнопки 'Заклади в ЖК' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Заклади в ЖК")
    try:
        await message.delete()
    except Exception:
        pass
    
    from database import get_all_general_services
    
    services = await get_all_general_services()
    
    if not services:
        admin_tag = CFG.admin_tag or "адміністратору"
        await render_or_edit(
            bot=message.bot,
            chat_id=message.chat.id,
            text=(
                "🏢 <b>Заклади в ЖК</b>\n\n"
                "Поки що категорій немає.\n\n"
                f"💬 Хочете додати категорію? Пишіть {admin_tag}"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="« Меню", callback_data="menu")]]
            ),
            context_key="places_menu",
        )
        return
    
    admin_tag = CFG.admin_tag or "адміністратору"
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=(
            "🏢 <b>Заклади в ЖК</b>\n\n"
            f"Оберіть категорію:\n\n"
            f"💬 Хочете додати категорію? Пишіть {admin_tag}"
        ),
        reply_markup=await get_places_keyboard(),
        context_key="places_menu",
    )


@router.callback_query(F.data == "places_menu")
async def cb_places_menu(callback: CallbackQuery):
    """Показати меню закладів."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Заклади в ЖК")
    from database import get_all_general_services
    
    services = await get_all_general_services()
    
    if not services:
        admin_tag = CFG.admin_tag or "адміністратору"
        await callback.message.edit_text(
            "🏢 <b>Заклади в ЖК</b>\n\n"
            "Поки що категорій немає.\n\n"
            f"💬 Хочете додати категорію? Пишіть {admin_tag}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Меню", callback_data="menu")],
            ])
        )
        await safe_callback_answer(callback)
        return
    
    admin_tag = CFG.admin_tag or "адміністратору"
    await callback.message.edit_text(
        "🏢 <b>Заклади в ЖК</b>\n\n"
        f"Оберіть категорію:\n\n"
        f"💬 Хочете додати категорію? Пишіть {admin_tag}",
        reply_markup=await get_places_keyboard()
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("places_cat_"))
async def cb_places_category(callback: CallbackQuery):
    """Показати заклади певної категорії."""
    from database import get_general_service, get_places_by_service_with_likes
    from business import get_business_service
    from business import is_business_feature_enabled

    service_id = int(callback.data.split("_")[2])
    service = await get_general_service(service_id)
    
    if not service:
        await safe_callback_answer(callback, "Категорію не знайдено", show_alert=True)
        return
    
    places = await get_places_by_service_with_likes(service_id)
    places = await get_business_service().enrich_places_for_main_bot(places)
    business_enabled = is_business_feature_enabled()
    admin_tag = CFG.admin_tag or "адміністратору"
    
    # Якщо повідомлення має фото - видаляємо і відправляємо нове
    is_photo = callback.message.photo is not None
    
    if not places:
        text = (
            f"🏢 <b>{service['name']}</b>\n\n"
            "Закладів поки немає.\n\n"
            f"💬 Хочете додати заклад? Пишіть {admin_tag}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="places_menu")],
        ])
        
        if is_photo:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text, reply_markup=keyboard)
        await safe_callback_answer(callback)
        return
    
    # Медалі для топ-3 у поточному порядку відображення.
    medals = ["🥇", "🥈", "🥉"]
    medal_map: dict[int, str] = {}

    # Важливо для "тихого" ввімкнення BUSINESS_MODE:
    # показувати business-рейтинг/медалі має сенс лише тоді, коли в категорії вже є хоча б 1 Verified.
    # Інакше медалі в категоріях з 0 лайків виглядають випадково і створюють UX-регресію.
    has_verified = bool(business_enabled and any(bool(item.get("is_verified")) for item in places))

    promo_slot_id = 0
    partner_slot_id = 0
    if business_enabled and has_verified:
        # Target catalog contract:
        # partner slot (single top Partner) -> promo slot (single top PRO) -> verified by likes -> unverified.
        # Defensive UI rule: even if legacy data has >1 partner, only one gets partner slot marker.
        verified_places = [item for item in places if item.get("is_verified")]
        unverified_places = [item for item in places if not item.get("is_verified")]

        def _is_active_pro_promo_slot(item: dict[str, Any]) -> bool:
            if str(item.get("verified_tier") or "").strip().lower() != "pro":
                return False
            raw_slot_until = str(item.get("promo_slot_until") or "").strip()
            if not raw_slot_until:
                # Legacy fallback: old Pro rows without promo_slot_until still behave as promo candidates.
                return True
            raw_normalized = f"{raw_slot_until[:-1]}+00:00" if raw_slot_until.endswith("Z") else raw_slot_until
            try:
                parsed = datetime.fromisoformat(raw_normalized)
            except Exception:
                # Invalid slot timestamp should not break catalog ordering.
                return False
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)

        partner_places = [item for item in verified_places if str(item.get("verified_tier") or "").strip().lower() == "partner"]
        pro_places = [item for item in verified_places if _is_active_pro_promo_slot(item)]
        other_verified = [
            item
            for item in verified_places
            if str(item.get("verified_tier") or "").strip().lower() != "partner" and not _is_active_pro_promo_slot(item)
        ]

        partner_places.sort(key=lambda item: (-(item.get("likes_count") or 0), item.get("name") or ""))
        pro_places.sort(key=lambda item: (-(item.get("likes_count") or 0), item.get("name") or ""))
        other_verified.sort(key=lambda item: (-(item.get("likes_count") or 0), item.get("name") or ""))
        unverified_places.sort(key=lambda item: (-(item.get("likes_count") or 0), item.get("name") or ""))

        partner_slot = partner_places[0] if partner_places else None
        partner_slot_id = int(partner_slot["id"]) if partner_slot else 0
        promo_slot = pro_places[0] if pro_places else None
        promo_slot_id = int(promo_slot["id"]) if promo_slot else 0

        verified_by_likes: list[dict] = []
        for item in partner_places:
            if int(item["id"]) == partner_slot_id:
                continue
            verified_by_likes.append(item)
        for item in pro_places:
            if int(item["id"]) == promo_slot_id:
                continue
            verified_by_likes.append(item)
        verified_by_likes.extend(other_verified)
        verified_by_likes.sort(key=lambda item: (-(item.get("likes_count") or 0), item.get("name") or ""))

        places = []
        if partner_slot:
            places.append(partner_slot)
        if promo_slot:
            places.append(promo_slot)
        places.extend(verified_by_likes)
        places.extend(unverified_places)

        # У business-режимі медалі відображають лише місця з реальними лайками (>0).
        medal_idx = 0
        for item in places:
            if medal_idx >= len(medals):
                break
            if int(item.get("likes_count") or 0) <= 0:
                continue
            try:
                medal_map[int(item["id"])] = medals[medal_idx]
                medal_idx += 1
            except Exception:
                continue
    else:
        # Legacy: медалі для топ-3 за лайками (і тільки якщо є лайки).
        top_by_likes = sorted(places, key=lambda item: -(item.get("likes_count") or 0))[:3]
        for idx, item in enumerate(top_by_likes):
            if (item.get("likes_count") or 0) <= 0:
                continue
            try:
                medal_map[int(item["id"])] = medals[idx]
            except Exception:
                continue
    
    # Показуємо кнопки з закладами
    buttons = []
    for place in places:
        place_id = int(place["id"])
        medal_prefix = medal_map.get(place_id)
        verified_prefix = None
        if business_enabled and has_verified and place.get("is_verified"):
            if int(place["id"]) == partner_slot_id:
                verified_prefix = "⭐"
            elif int(place["id"]) == promo_slot_id:
                verified_prefix = "🔝"
            else:
                verified_prefix = "✅"
        prefix_parts = [p for p in [medal_prefix, verified_prefix] if p]
        prefix = (" ".join(prefix_parts) + " ") if prefix_parts else ""
        
        # Показуємо кількість лайків
        likes_text = f" ❤️{place['likes_count']}" if place["likes_count"] > 0 else ""
        tier_badge = ""
        if business_enabled and has_verified and int(place["id"]) == partner_slot_id:
            tier_badge = " • Офіційний партнер"

        label = f"{prefix}{place['name']}{tier_badge}{likes_text}"
        cb = f"place_{place['id']}"

        # Optional: highlight only top paid tiers to make them stand out,
        # but keep it subtle (<=2 colored buttons per category).
        btn: InlineKeyboardButton
        btn_style: str | None = None
        if business_enabled and has_verified and place.get("is_verified"):
            if int(place["id"]) == partner_slot_id:
                btn_style = STYLE_SUCCESS
            elif int(place["id"]) == promo_slot_id:
                btn_style = STYLE_PRIMARY

        if btn_style:
            btn = ikb(text=label, callback_data=cb, style=btn_style)
        else:
            btn = InlineKeyboardButton(text=label, callback_data=cb)

        buttons.append([btn])
    
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="places_menu")])
    
    ranking_hint = ""
    if business_enabled and has_verified:
        ranking_hint = "⭐ офіційний партнер • 🔝 промо • ✅ verified\n\n"
    text = (
        f"🏢 <b>{service['name']}</b>\n\n"
        f"Оберіть заклад (❤️ = лайки мешканців):\n\n"
        f"{ranking_hint}"
        f"💬 Побачили помилку? Пишіть {admin_tag}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if is_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=keyboard)
    else:
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    await safe_callback_answer(callback)


def _normalize_place_link(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if any(ch.isspace() for ch in value):
        return None
    lowered = value.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("tg://"):
        return value
    if lowered.startswith("t.me/"):
        return "https://" + value
    if value.startswith("@"):
        username = value[1:].strip()
        if username:
            return f"https://t.me/{username}"
        return None
    # Plain username (best-effort).
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        return f"https://t.me/{value}"
    # Plain host/path (best-effort): example.com/path -> https://example.com/path
    if PLAIN_HOST_WITH_PATH_RE.fullmatch(value):
        return "https://" + value
    return None


def _is_telegram_file_id(raw: str | None) -> bool:
    value = str(raw or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("tg://")
        or lowered.startswith("t.me/")
        or value.startswith("@")
    ):
        return False
    return bool(TELEGRAM_FILE_ID_RE.fullmatch(value))


def _resolve_place_media_target(raw: str | None) -> tuple[str, str] | None:
    url = _normalize_place_link(raw)
    if url:
        return ("url", url)
    value = str(raw or "").strip()
    if _is_telegram_file_id(value):
        return ("file_id", value)
    return None


async def _open_place_media_target(
    callback: CallbackQuery,
    *,
    place_id: int,
    raw_media_value: str | None,
    missing_message: str,
    fallback_label: str,
) -> bool:
    target = _resolve_place_media_target(raw_media_value)
    if not target:
        await safe_callback_answer(callback, missing_message, show_alert=True)
        return False

    target_type, target_value = target
    if target_type == "url":
        try:
            return await _render_external_open_panel(
                callback,
                place_id=place_id,
                title=f"🖼 <b>{html.escape(fallback_label)}</b>",
                button_text=f"🖼 Відкрити {fallback_label}",
                url=target_value,
            )
        except Exception:
            logger.exception("Failed to open place media URL panel for place_id=%s", place_id)
            await safe_callback_answer(
                callback,
                f"Не вдалося відкрити «{fallback_label}». Спробуйте ще раз.",
                show_alert=True,
            )
            return False

    # Telegram file_id path.
    try:
        if callback.message:
            await callback.message.answer_photo(photo=target_value)
            await safe_callback_answer(callback)
            return True
        await safe_callback_answer(callback, "Не вдалося відкрити медіа.", show_alert=True)
        return False
    except Exception:
        await safe_callback_answer(callback, "Не вдалося відкрити медіа.", show_alert=True)
        return False


async def _render_external_open_panel(
    callback: CallbackQuery,
    *,
    place_id: int,
    title: str,
    button_text: str,
    url: str,
) -> bool:
    """Render single-message external link panel with a back button."""
    if not callback.message:
        await safe_callback_answer(callback, "Не вдалося відкрити посилання.", show_alert=True)
        return False
    panel_text = f"{title}\n\nНатисніть кнопку нижче, щоб відкрити."
    panel_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text, url=url)],
            [InlineKeyboardButton(text="« До картки", callback_data=f"place_{int(place_id)}")],
        ]
    )
    try:
        if getattr(callback.message, "photo", None):
            await callback.message.edit_caption(
                caption=panel_text,
                reply_markup=panel_markup,
            )
        else:
            await callback.message.edit_text(
                panel_text,
                reply_markup=panel_markup,
            )
        await safe_callback_answer(callback)
        return True
    except Exception:
        logger.exception("Failed to render external open panel; using fallback for place_id=%s", place_id)
        # Fallback 1: open URL directly via callback query (no extra chat message).
        try:
            await safe_callback_answer(callback, url=url)
            return True
        except Exception:
            logger.exception("Failed direct callback URL fallback for place_id=%s", place_id)

        # Fallback 2: send standalone message with URL button.
        try:
            await callback.message.answer(panel_text, reply_markup=panel_markup)
            await safe_callback_answer(callback)
            return True
        except Exception:
            logger.exception("Failed message fallback for external open panel, place_id=%s", place_id)

        await safe_callback_answer(callback, "Не вдалося відкрити посилання.", show_alert=True)
        return False


def _normalize_tel_url(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    # Keep digits and leading "+" only.
    cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "+")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if len(digits) < 7:
        return None
    return f"tel:{cleaned}"


def build_place_detail_keyboard(
    place_enriched: dict,
    *,
    likes_count: int,
    user_liked: bool,
    business_enabled: bool,
    gallery_items: list[dict] | None = None,
) -> InlineKeyboardMarkup:
    place_id = int(place_enriched["id"])
    service_id = int(place_enriched["service_id"])

    # Contact/link buttons are shown only for Verified places in business mode.
    action_buttons: list[InlineKeyboardButton] = []
    if business_enabled and place_enriched.get("is_verified"):
        tier = str(place_enriched.get("verified_tier") or "").strip().lower()
        contact_type = str(place_enriched.get("contact_type") or "").strip().lower()
        contact_value = str(place_enriched.get("contact_value") or "").strip()
        if contact_type == "call" and contact_value:
            tel_url = _normalize_tel_url(contact_value)
            if tel_url:
                # Use callback for tracked opens (action=call).
                action_buttons.append(InlineKeyboardButton(text="📞 Подзвонити", callback_data=f"pcall_{place_id}"))
        elif contact_type == "chat" and contact_value:
            chat_url = _normalize_place_link(contact_value)
            if chat_url:
                # Use callback for tracked opens (action=chat) and then redirect via answer_callback_query(url=...).
                action_buttons.append(InlineKeyboardButton(text="💬 Написати", callback_data=f"pchat_{place_id}"))

        link_url = _normalize_place_link(place_enriched.get("link_url"))
        if link_url:
            # Track link opens (action=link) and then redirect.
            action_buttons.append(InlineKeyboardButton(text="🔗 Посилання", callback_data=f"plink_{place_id}"))
        logo_target = _resolve_place_media_target(place_enriched.get("logo_url"))
        if logo_target:
            # Track logo opens (action=logo_open) and then redirect.
            action_buttons.append(InlineKeyboardButton(text="🖼 Логотип/фото", callback_data=f"plogo_{place_id}"))
        if gallery_items:
            for idx, item in enumerate(gallery_items[:6], start=1):
                media_id = int(item.get("id") or 0)
                if media_id <= 0:
                    continue
                action_buttons.append(
                    InlineKeyboardButton(
                        text=f"📷 Фото {idx}",
                        callback_data=f"pgm_{place_id}_{media_id}",
                    )
                )

        # Premium/Partner extra CTA buttons.
        if tier in {"pro", "partner"}:
            menu_url = _normalize_place_link(place_enriched.get("menu_url"))
            if menu_url:
                action_buttons.append(InlineKeyboardButton(text="📋 Меню/Прайс", callback_data=f"pmenu_{place_id}"))
            order_url = _normalize_place_link(place_enriched.get("order_url"))
            if order_url:
                action_buttons.append(InlineKeyboardButton(text="🛒 Замовити/Запис", callback_data=f"porder_{place_id}"))
            offer_1_image_target = _resolve_place_media_target(place_enriched.get("offer_1_image_url"))
            if offer_1_image_target:
                action_buttons.append(InlineKeyboardButton(text="🖼 Фото оферу 1", callback_data=f"pmimg1_{place_id}"))
            offer_2_image_target = _resolve_place_media_target(place_enriched.get("offer_2_image_url"))
            if offer_2_image_target:
                action_buttons.append(InlineKeyboardButton(text="🖼 Фото оферу 2", callback_data=f"pmimg2_{place_id}"))
        if tier == "partner":
            partner_photo_1_target = _resolve_place_media_target(place_enriched.get("photo_1_url"))
            if partner_photo_1_target:
                action_buttons.append(InlineKeyboardButton(text="📸 Фото 1", callback_data=f"pph1_{place_id}"))
            partner_photo_2_target = _resolve_place_media_target(place_enriched.get("photo_2_url"))
            if partner_photo_2_target:
                action_buttons.append(InlineKeyboardButton(text="📸 Фото 2", callback_data=f"pph2_{place_id}"))
            partner_photo_3_target = _resolve_place_media_target(place_enriched.get("photo_3_url"))
            if partner_photo_3_target:
                action_buttons.append(InlineKeyboardButton(text="📸 Фото 3", callback_data=f"pph3_{place_id}"))

    # Like button.
    if user_liked:
        like_btn = InlineKeyboardButton(text=f"💔 Забрати лайк ({likes_count})", callback_data=f"unlike_{place_id}")
    else:
        like_btn = InlineKeyboardButton(text=f"❤️ Подобається ({likes_count})", callback_data=f"like_{place_id}")

    rows: list[list[InlineKeyboardButton]] = []
    if action_buttons:
        # Keep at most 2 buttons in a row to avoid cramped UI.
        for idx in range(0, len(action_buttons), 2):
            rows.append(action_buttons[idx : idx + 2])
    promo_code = str(place_enriched.get("promo_code") or "").strip()
    if business_enabled and place_enriched.get("is_verified") and promo_code:
        rows.append([InlineKeyboardButton(text="🎟 Відкрити промокод", callback_data=f"pcoupon_{place_id}")])
    rows.append([like_btn])
    rows.append([InlineKeyboardButton(text="⚠️ Запропонувати правку", callback_data=f"plrep_{place_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"places_cat_{service_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_place_detail_message(message: Message, *, place_id: int, user_id: int) -> bool:
    """Render place detail in-place. Returns False when place is unavailable."""
    from database import (
        get_general_service,
        get_place,
        get_place_gallery_media,
        get_place_likes_count,
        has_liked_place,
        record_place_view,
    )
    from business import get_business_service, is_business_feature_enabled

    place = await get_place(place_id)
    if not place:
        return False
    service = await get_general_service(int(place.get("service_id") or 0))
    service_name = str(service.get("name") or "").strip() if service else ""

    # Best-effort analytics: do not break UX on failure.
    await record_place_view(place_id)

    admin_tag = CFG.admin_tag or "адміністратору"

    user_liked = await has_liked_place(place_id, user_id)
    likes_count = await get_place_likes_count(place_id)

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    text = f"🏢 <b>{place_enriched['name']}</b>\n\n"
    if service_name:
        text += f"🗂 <b>Категорія:</b> {html.escape(service_name)}\n\n"
    if is_business_feature_enabled() and place_enriched.get("is_verified"):
        tier_norm = str(place_enriched.get("verified_tier") or "").strip().lower()
        if tier_norm == "partner":
            text += "⭐ <b>Офіційний партнер категорії</b>\n\n"
        else:
            tier = _resident_verified_tier_title(tier_norm)
            tier_text = f" {tier}" if tier else ""
            text += f"✅ <b>Verified{tier_text}</b>\n\n"

        opening_hours = str(place_enriched.get("opening_hours") or "").strip()
        if opening_hours:
            text += f"⏰ <b>Години:</b> {html.escape(opening_hours)}\n\n"

        promo_code = str(place_enriched.get("promo_code") or "").strip()
        if promo_code:
            text += f"🎟 <b>Промокод:</b> <code>{html.escape(promo_code)}</code>\n\n"

        tier_for_offers = str(place_enriched.get("verified_tier") or "").strip().lower()
        if tier_for_offers in {"pro", "partner"}:
            offer_1 = str(place_enriched.get("offer_1_text") or "").strip()
            offer_2 = str(place_enriched.get("offer_2_text") or "").strip()
            offer_lines: list[str] = []
            if offer_1:
                offer_lines.append(f"• {html.escape(offer_1)}")
            if offer_2:
                offer_lines.append(f"• {html.escape(offer_2)}")
            if offer_lines:
                text += "🎁 <b>Акції та офери:</b>\n" + "\n".join(offer_lines) + "\n\n"

    if place_enriched["description"]:
        text += f"📝 {place_enriched['description']}\n\n"

    if place_enriched["address"]:
        text += f"📍 <b>Адреса:</b> {place_enriched['address']}\n\n"

    text += f"❤️ <b>Лайків:</b> {likes_count}\n\n"
    text += f"💬 Побачили помилку? Хочете додати детальніший опис? Пишіть {admin_tag}"

    map_file = get_map_file_for_address(place_enriched["address"])

    gallery_items: list[dict] = []
    if is_business_feature_enabled() and place_enriched.get("is_verified"):
        try:
            gallery_items = await get_place_gallery_media(int(place_id), limit=8)
        except Exception:
            logger.exception("Failed to load place gallery media place_id=%s", place_id)
            gallery_items = []

    keyboard = build_place_detail_keyboard(
        place_enriched,
        likes_count=likes_count,
        user_liked=user_liked,
        business_enabled=is_business_feature_enabled(),
        gallery_items=gallery_items,
    )

    if map_file:
        try:
            await message.delete()
        except Exception:
            pass

        photo = FSInputFile(map_file)
        await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await message.answer(text, reply_markup=keyboard)
    return True


@router.callback_query(F.data.startswith("place_"))
async def cb_place_detail(callback: CallbackQuery):
    """Показати інформацію про заклад з картою."""
    try:
        place_id = int(callback.data.split("_")[1])
    except Exception:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    shown = await _render_place_detail_message(
        callback.message,
        place_id=place_id,
        user_id=int(callback.from_user.id),
    )
    if not shown:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return
    await safe_callback_answer(callback)


def _build_place_report_keyboard(place_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"plrep_cancel_{place_id}")],
            [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
        ]
    )


@router.callback_query(F.data.regexp(r"^plrep_\d+$"))
async def cb_place_report_start(callback: CallbackQuery, state: FSMContext) -> None:
    from database import get_place

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    search_waiting_users.discard(callback.message.chat.id)
    await state.set_state(PlaceReportStates.waiting_for_text)
    await state.update_data(place_report_place_id=place_id)

    text = (
        "📝 <b>Запропонувати правку</b>\n\n"
        f"Заклад: <b>{html.escape(str(place.get('name') or '—'))}</b>\n\n"
        "Опишіть, що потрібно виправити в картці закладу.\n"
        "Наприклад: графік роботи, контакти, опис або адресу.\n\n"
        "Ліміт: до 600 символів."
    )
    kb = _build_place_report_keyboard(place_id)
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("plrep_cancel_"))
async def cb_place_report_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        place_id = int(callback.data.split("_", 2)[2])
    except Exception:
        place_id = 0
    await state.clear()
    if place_id > 0:
        shown = await _render_place_detail_message(
            callback.message,
            place_id=place_id,
            user_id=int(callback.from_user.id),
        )
        if not shown:
            await safe_callback_answer(callback, "Скасовано")
            return
    else:
        await callback.message.edit_text(
            "Скасовано.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
            ),
        )
    await safe_callback_answer(callback, "Скасовано")


@router.message(PlaceReportStates.waiting_for_text, F.text & ~F.text.startswith("/"))
async def msg_place_report_submit(message: Message, state: FSMContext) -> None:
    from database import create_place_report, create_admin_job, get_place

    try:
        await message.delete()
    except Exception:
        pass
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Порожній текст. Опишіть, що потрібно виправити.")
        return
    if len(raw) > 600:
        await message.answer("❌ Занадто довгий текст. Максимум 600 символів.")
        return

    data = await state.get_data()
    place_id = int(data.get("place_report_place_id") or 0)
    if place_id <= 0:
        await state.clear()
        await message.answer(
            "❌ Не вдалося визначити заклад. Спробуйте ще раз.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
            ),
        )
        return

    from_user = message.from_user
    report = await create_place_report(
        place_id=place_id,
        reporter_tg_user_id=int(from_user.id if from_user else message.chat.id),
        reporter_username=str(from_user.username or "") if from_user else "",
        reporter_first_name=str(from_user.first_name or "") if from_user else "",
        reporter_last_name=str(from_user.last_name or "") if from_user else "",
        report_text=raw,
    )
    if not report:
        await state.clear()
        await message.answer(
            "❌ Заклад не знайдено або він недоступний.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
            ),
        )
        return

    place = await get_place(place_id)
    place_name = str((place or {}).get("name") or f"ID {place_id}")
    payload = {
        "report_id": int(report["id"]),
        "place_id": place_id,
        "place_name": place_name,
        "reporter_tg_user_id": int(from_user.id if from_user else message.chat.id),
        "reporter_username": str(from_user.username or "") if from_user else "",
        "reporter_first_name": str(from_user.first_name or "") if from_user else "",
        "reporter_last_name": str(from_user.last_name or "") if from_user else "",
        "report_text": raw,
        "created_at": str(report.get("created_at") or ""),
    }
    try:
        await create_admin_job(
            "admin_place_report_alert",
            payload,
            created_by=int(from_user.id if from_user else message.chat.id),
        )
    except Exception:
        logger.exception("Failed to enqueue admin_place_report_alert report_id=%s", report.get("id"))

    await state.clear()
    await message.answer(
        "✅ Дякуємо! Передали правку адміну на модерацію.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 До закладу", callback_data=f"place_{place_id}")],
                [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
            ]
        ),
    )


@router.message(PlaceReportStates.waiting_for_text)
async def msg_place_report_non_text(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("📝 Надішліть текст правки або натисніть «Скасувати».")


@router.callback_query(F.data.startswith("pcoupon_"))
async def cb_place_coupon_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    promo_code = str(place_enriched.get("promo_code") or "").strip()
    if not (is_business_feature_enabled() and place_enriched.get("is_verified") and promo_code):
        await safe_callback_answer(callback, "Промокод для цього закладу недоступний.", show_alert=True)
        return

    await record_place_click(place_id, "coupon_open")
    await safe_callback_answer(callback, f"🎟 Промокод: {promo_code}", show_alert=True)


@router.callback_query(F.data.startswith("pchat_"))
async def cb_place_chat_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Чат для цього закладу недоступний.", show_alert=True)
        return

    contact_type = str(place_enriched.get("contact_type") or "").strip().lower()
    contact_value = str(place_enriched.get("contact_value") or "").strip()
    if contact_type != "chat" or not contact_value:
        await safe_callback_answer(callback, "Чат для цього закладу недоступний.", show_alert=True)
        return

    chat_url = _normalize_place_link(contact_value)
    if not chat_url:
        await safe_callback_answer(callback, "Некоректне посилання на чат.", show_alert=True)
        return

    await record_place_click(place_id, "chat")
    await _render_external_open_panel(
        callback,
        place_id=place_id,
        title="💬 <b>Чат закладу</b>",
        button_text="💬 Написати",
        url=chat_url,
    )


@router.callback_query(F.data.startswith("pcall_"))
async def cb_place_call_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Контакт для цього закладу недоступний.", show_alert=True)
        return

    contact_type = str(place_enriched.get("contact_type") or "").strip().lower()
    contact_value = str(place_enriched.get("contact_value") or "").strip()
    if contact_type != "call" or not contact_value:
        await safe_callback_answer(callback, "Контакт для цього закладу недоступний.", show_alert=True)
        return

    tel_url = _normalize_tel_url(contact_value)
    if not tel_url:
        await safe_callback_answer(callback, "Некоректний номер телефону.", show_alert=True)
        return

    await record_place_click(place_id, "call")
    await _render_external_open_panel(
        callback,
        place_id=place_id,
        title="📞 <b>Дзвінок у заклад</b>",
        button_text="📞 Подзвонити",
        url=tel_url,
    )


@router.callback_query(F.data.startswith("plink_"))
async def cb_place_link_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Посилання для цього закладу недоступне.", show_alert=True)
        return

    link_url = _normalize_place_link(place_enriched.get("link_url"))
    if not link_url:
        await safe_callback_answer(callback, "Некоректне посилання.", show_alert=True)
        return

    await record_place_click(place_id, "link")
    await _render_external_open_panel(
        callback,
        place_id=place_id,
        title="🔗 <b>Посилання закладу</b>",
        button_text="🔗 Відкрити посилання",
        url=link_url,
    )


@router.callback_query(F.data.startswith("plogo_"))
async def cb_place_logo_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Логотип для цього закладу недоступний.", show_alert=True)
        return

    await record_place_click(place_id, "logo_open")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("logo_url"),
        missing_message="Логотип для цього закладу відсутній або некоректний.",
        fallback_label="Логотип/фото",
    )


@router.callback_query(F.data.startswith("pmenu_"))
async def cb_place_menu_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Меню для цього закладу недоступне.", show_alert=True)
        return

    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier not in {"pro", "partner"}:
        await safe_callback_answer(callback, "Меню для цього закладу недоступне.", show_alert=True)
        return

    menu_url = _normalize_place_link(place_enriched.get("menu_url"))
    if not menu_url:
        await safe_callback_answer(callback, "Некоректне посилання на меню.", show_alert=True)
        return

    await record_place_click(place_id, "menu")
    await _render_external_open_panel(
        callback,
        place_id=place_id,
        title="📋 <b>Меню / прайс</b>",
        button_text="📋 Відкрити меню/прайс",
        url=menu_url,
    )


@router.callback_query(F.data.startswith("porder_"))
async def cb_place_order_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Замовлення для цього закладу недоступне.", show_alert=True)
        return

    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier not in {"pro", "partner"}:
        await safe_callback_answer(callback, "Замовлення для цього закладу недоступне.", show_alert=True)
        return

    order_url = _normalize_place_link(place_enriched.get("order_url"))
    if not order_url:
        await safe_callback_answer(callback, "Некоректне посилання на замовлення.", show_alert=True)
        return

    await record_place_click(place_id, "order")
    await _render_external_open_panel(
        callback,
        place_id=place_id,
        title="🛒 <b>Замовлення / запис</b>",
        button_text="🛒 Відкрити замовлення/запис",
        url=order_url,
    )


@router.callback_query(F.data.startswith("pmimg1_"))
async def cb_place_offer_1_image_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Фото оферу недоступне.", show_alert=True)
        return
    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier not in {"pro", "partner"}:
        await safe_callback_answer(callback, "Фото оферу недоступне.", show_alert=True)
        return

    await record_place_click(place_id, "offer1_image")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("offer_1_image_url"),
        missing_message="Фото оферу 1 відсутнє або некоректне.",
        fallback_label="Фото оферу 1",
    )


@router.callback_query(F.data.startswith("pmimg2_"))
async def cb_place_offer_2_image_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Фото оферу недоступне.", show_alert=True)
        return
    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier not in {"pro", "partner"}:
        await safe_callback_answer(callback, "Фото оферу недоступне.", show_alert=True)
        return

    await record_place_click(place_id, "offer2_image")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("offer_2_image_url"),
        missing_message="Фото оферу 2 відсутнє або некоректне.",
        fallback_label="Фото оферу 2",
    )


@router.callback_query(F.data.startswith("pph1_"))
async def cb_place_partner_photo_1_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return
    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier != "partner":
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return

    await record_place_click(place_id, "partner_photo_1")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("photo_1_url"),
        missing_message="Фото 1 відсутнє або некоректне.",
        fallback_label="Фото 1",
    )


@router.callback_query(F.data.startswith("pph2_"))
async def cb_place_partner_photo_2_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return
    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier != "partner":
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return

    await record_place_click(place_id, "partner_photo_2")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("photo_2_url"),
        missing_message="Фото 2 відсутнє або некоректне.",
        fallback_label="Фото 2",
    )


@router.callback_query(F.data.startswith("pph3_"))
async def cb_place_partner_photo_3_open(callback: CallbackQuery) -> None:
    from database import get_place, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        place_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return
    tier = str(place_enriched.get("verified_tier") or "").strip().lower()
    if tier != "partner":
        await safe_callback_answer(callback, "Фото недоступне.", show_alert=True)
        return

    await record_place_click(place_id, "partner_photo_3")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=place_enriched.get("photo_3_url"),
        missing_message="Фото 3 відсутнє або некоректне.",
        fallback_label="Фото 3",
    )


@router.callback_query(F.data.regexp(r"^pgm_\d+_\d+$"))
async def cb_place_gallery_media_open(callback: CallbackQuery) -> None:
    from database import get_place, get_place_gallery_media, record_place_click
    from business import get_business_service, is_business_feature_enabled

    try:
        _, place_raw, media_raw = str(callback.data or "").split("_", 2)
        place_id = int(place_raw)
        media_id = int(media_raw)
    except Exception:
        await safe_callback_answer(callback, "❌ Некоректний запит", show_alert=True)
        return

    place = await get_place(place_id)
    if not place:
        await safe_callback_answer(callback, "Заклад не знайдено", show_alert=True)
        return

    place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
    if not (is_business_feature_enabled() and place_enriched.get("is_verified")):
        await safe_callback_answer(callback, "Галерея для цього закладу недоступна.", show_alert=True)
        return

    gallery_items = await get_place_gallery_media(place_id, limit=50)
    media_item = next((row for row in gallery_items if int(row.get("id") or 0) == int(media_id)), None)
    if not media_item:
        await safe_callback_answer(callback, "Фото не знайдено.", show_alert=True)
        return

    await record_place_click(place_id, "gallery_open")
    await _open_place_media_target(
        callback,
        place_id=place_id,
        raw_media_value=media_item.get("media_ref"),
        missing_message="Фото галереї відсутнє або некоректне.",
        fallback_label="Фото галереї",
    )


@router.callback_query(F.data.startswith("like_"))
async def cb_like_place(callback: CallbackQuery):
    """Поставити лайк закладу."""
    from database import like_place, get_place_likes_count, get_place, get_place_gallery_media
    
    place_id = int(callback.data.split("_")[1])
    
    # Ставимо лайк
    added = await like_place(place_id, callback.from_user.id)
    
    if added:
        likes_count = await get_place_likes_count(place_id)
        await safe_callback_answer(callback, f"❤️ Дякуємо за лайк! Усього: {likes_count}")
    else:
        await safe_callback_answer(callback, "Ви вже лайкнули цей заклад")
    
    # Оновлюємо кнопку (+ optional paid buttons)
    place = await get_place(place_id)
    if place:
        likes_count = await get_place_likes_count(place_id)
        from business import get_business_service, is_business_feature_enabled
        place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
        gallery_items: list[dict] = []
        if is_business_feature_enabled() and place_enriched.get("is_verified"):
            try:
                gallery_items = await get_place_gallery_media(int(place_id), limit=8)
            except Exception:
                gallery_items = []
        new_keyboard = build_place_detail_keyboard(
            place_enriched,
            likes_count=likes_count,
            user_liked=True,
            business_enabled=is_business_feature_enabled(),
            gallery_items=gallery_items,
        )
        
        try:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=callback.message.caption,
                    reply_markup=new_keyboard
                )
            else:
                await callback.message.edit_reply_markup(reply_markup=new_keyboard)
        except Exception:
            pass


@router.callback_query(F.data.startswith("unlike_"))
async def cb_unlike_place(callback: CallbackQuery):
    """Забрати лайк із закладу."""
    from database import unlike_place, get_place_likes_count, get_place, get_place_gallery_media
    
    place_id = int(callback.data.split("_")[1])
    
    # Забираємо лайк
    removed = await unlike_place(place_id, callback.from_user.id)
    
    if removed:
        likes_count = await get_place_likes_count(place_id)
        await safe_callback_answer(callback, f"💔 Лайк забрано. Усього: {likes_count}")
    else:
        await safe_callback_answer(callback, "Ви не лайкали цей заклад")
    
    # Оновлюємо кнопку (+ optional paid buttons)
    place = await get_place(place_id)
    if place:
        likes_count = await get_place_likes_count(place_id)
        from business import get_business_service, is_business_feature_enabled
        place_enriched = (await get_business_service().enrich_places_for_main_bot([place]))[0]
        gallery_items: list[dict] = []
        if is_business_feature_enabled() and place_enriched.get("is_verified"):
            try:
                gallery_items = await get_place_gallery_media(int(place_id), limit=8)
            except Exception:
                gallery_items = []
        new_keyboard = build_place_detail_keyboard(
            place_enriched,
            likes_count=likes_count,
            user_liked=False,
            business_enabled=is_business_feature_enabled(),
            gallery_items=gallery_items,
        )
        
        try:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=callback.message.caption,
                    reply_markup=new_keyboard
                )
            else:
                await callback.message.edit_reply_markup(reply_markup=new_keyboard)
        except Exception:
            pass


## Place/category admin commands were removed from the main user-bot.
## They will be implemented in the separate admin-bot (control-plane) later.


# ============ Голосування за опалення та воду ============

def get_heating_vote_keyboard(user_vote: bool | None = None) -> InlineKeyboardMarkup:
    """Клавіатура для голосування за опалення (в меню з оновленням статусу)."""
    yes_text = "✅ Є опалення" if user_vote is True else "🔥 Є опалення"
    no_text = "✅ Немає опалення" if user_vote is False else "❄️ Немає опалення"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=yes_text, callback_data="menu_vote_heating_yes"),
            InlineKeyboardButton(text=no_text, callback_data="menu_vote_heating_no"),
        ],
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="heating_menu")],
        [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
    ])


def get_water_vote_keyboard(user_vote: bool | None = None) -> InlineKeyboardMarkup:
    """Клавіатура для голосування за воду (в меню з оновленням статусу)."""
    yes_text = "✅ Є вода" if user_vote is True else "💧 Є вода"
    no_text = "✅ Немає води" if user_vote is False else "🚫 Немає води"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=yes_text, callback_data="menu_vote_water_yes"),
            InlineKeyboardButton(text=no_text, callback_data="menu_vote_water_no"),
        ],
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="water_menu")],
        [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
    ])


async def format_heating_status(user_id: int) -> str:
    """Форматувати статус опалення на основі голосів по будинку користувача."""
    from database import (
        get_heating_stats,
        get_subscriber_building_and_section,
        get_building_by_id,
        is_valid_section_for_building,
    )
    
    building_id, section_id = await get_subscriber_building_and_section(user_id)
    building = get_building_by_id(building_id) if building_id else None
    
    if not building:
        return (
            "🔥 <b>Стан опалення</b>\n\n"
            "⚠️ Ви ще не обрали свій будинок.\n"
            "Натисніть «🏠 Обрати будинок» щоб голосувати по вашому будинку."
        )
    if not is_valid_section_for_building(building_id, section_id):
        return (
            "🔥 <b>Стан опалення</b>\n\n"
            f"🏠 {building['name']} ({building['address']})\n\n"
            "⚠️ Ви ще не обрали секцію.\n"
            "Натисніть «🏠 Обрати будинок» і оберіть секцію."
        )
    
    building_name = f"{building['name']} ({building['address']}), секція {section_id}"
    stats = await get_heating_stats(building_id, section_id)
    
    if stats["total"] == 0:
        return (
            f"🔥 <b>Стан опалення</b>\n"
            f"🏠 {building_name}\n\n"
            "🤷 Ще ніхто не голосував.\n\n"
            "👇 <b>Допоможи сусідам!</b>\n"
            "Повідом, чи є у тебе опалення:"
        )
    
    # Візуальний прогрес-бар
    bar_length = 10
    has_blocks = round(stats["has_percent"] / 100 * bar_length)
    bar = "🟩" * has_blocks + "🟥" * (bar_length - has_blocks)
    
    return (
        f"🔥 <b>Стан опалення</b>\n"
        f"🏠 {building_name}\n\n"
        f"{bar}\n\n"
        f"✅ Є опалення: <b>{stats['has_percent']}%</b> ({stats['has']} голосів)\n"
        f"❄️ Немає опалення: <b>{stats['has_not_percent']}%</b> ({stats['has_not']} голосів)\n\n"
        f"📊 Всього проголосувало: {stats['total']} мешканців\n\n"
        "👇 <b>А у тебе є опалення?</b>"
    )


async def format_water_status(user_id: int) -> str:
    """Форматувати статус води на основі голосів по будинку користувача."""
    from database import (
        get_water_stats,
        get_subscriber_building_and_section,
        get_building_by_id,
        is_valid_section_for_building,
    )
    
    building_id, section_id = await get_subscriber_building_and_section(user_id)
    building = get_building_by_id(building_id) if building_id else None
    
    if not building:
        return (
            "💧 <b>Стан води</b>\n\n"
            "⚠️ Ви ще не обрали свій будинок.\n"
            "Натисніть «🏠 Обрати будинок» щоб голосувати по вашому будинку."
        )
    if not is_valid_section_for_building(building_id, section_id):
        return (
            "💧 <b>Стан води</b>\n\n"
            f"🏠 {building['name']} ({building['address']})\n\n"
            "⚠️ Ви ще не обрали секцію.\n"
            "Натисніть «🏠 Обрати будинок» і оберіть секцію."
        )
    
    building_name = f"{building['name']} ({building['address']}), секція {section_id}"
    stats = await get_water_stats(building_id, section_id)
    
    if stats["total"] == 0:
        return (
            f"💧 <b>Стан води</b>\n"
            f"🏠 {building_name}\n\n"
            "🤷 Ще ніхто не голосував.\n\n"
            "👇 <b>Допоможи сусідам!</b>\n"
            "Повідом, чи є у тебе вода:"
        )
    
    # Візуальний прогрес-бар
    bar_length = 10
    has_blocks = round(stats["has_percent"] / 100 * bar_length)
    bar = "🟩" * has_blocks + "🟥" * (bar_length - has_blocks)
    
    return (
        f"💧 <b>Стан води</b>\n"
        f"🏠 {building_name}\n\n"
        f"{bar}\n\n"
        f"✅ Є вода: <b>{stats['has_percent']}%</b> ({stats['has']} голосів)\n"
        f"🚫 Немає води: <b>{stats['has_not_percent']}%</b> ({stats['has_not']} голосів)\n\n"
        f"📊 Всього проголосувало: {stats['total']} мешканців\n\n"
        "👇 <b>А у тебе є вода?</b>"
    )


### Обробники reply_heating та reply_water видалено - тепер ці функції доступні через
### підменю "💡 Світло/опалення/вода" (callback cb_utilities_menu) ###


@router.callback_query(F.data == "heating_menu")
async def cb_heating_menu(callback: CallbackQuery):
    """Показати меню опалення."""
    from database import get_user_vote
    user_vote = await get_user_vote(callback.message.chat.id, "heating")
    text = await format_heating_status(callback.message.chat.id)
    
    # Оновлено вже додається у format_light_status
    
    try:
        await callback.message.edit_text(text, reply_markup=get_heating_vote_keyboard(user_vote))
    except Exception:
        # Якщо повідомлення не змінилось
        pass
    await safe_callback_answer(callback)


@router.callback_query(F.data == "water_menu")
async def cb_water_menu(callback: CallbackQuery):
    """Показати меню води."""
    from database import get_user_vote
    user_vote = await get_user_vote(callback.message.chat.id, "water")
    text = await format_water_status(callback.message.chat.id)
    
    now = datetime.now().strftime("%H:%M:%S")
    text += f"\n\n<i>Оновлено: {now}</i>"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_water_vote_keyboard(user_vote))
    except Exception:
        pass
    await safe_callback_answer(callback)


# --- Голосування зі сповіщень (без оновлення повідомлення) ---

@router.callback_query(F.data == "vote_heating_yes")
async def cb_vote_heating_yes(callback: CallbackQuery):
    """Проголосувати: є опалення (зі сповіщення)."""
    from database import vote_heating
    await vote_heating(callback.message.chat.id, True)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що опалення є.", show_alert=True)


@router.callback_query(F.data == "vote_heating_no")
async def cb_vote_heating_no(callback: CallbackQuery):
    """Проголосувати: немає опалення (зі сповіщення)."""
    from database import vote_heating
    await vote_heating(callback.message.chat.id, False)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що опалення немає.", show_alert=True)


@router.callback_query(F.data == "vote_water_yes")
async def cb_vote_water_yes(callback: CallbackQuery):
    """Проголосувати: є вода (зі сповіщення)."""
    from database import vote_water
    await vote_water(callback.message.chat.id, True)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що вода є.", show_alert=True)


@router.callback_query(F.data == "vote_water_no")
async def cb_vote_water_no(callback: CallbackQuery):
    """Проголосувати: немає води (зі сповіщення)."""
    from database import vote_water
    await vote_water(callback.message.chat.id, False)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що води немає.", show_alert=True)


# --- Голосування з меню (з оновленням статусу) ---

@router.callback_query(F.data == "menu_vote_heating_yes")
async def cb_menu_vote_heating_yes(callback: CallbackQuery):
    """Проголосувати: є опалення (з меню, оновлює статус)."""
    from database import vote_heating, get_user_vote
    await vote_heating(callback.message.chat.id, True)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що опалення є.", show_alert=True)
    
    user_vote = await get_user_vote(callback.message.chat.id, "heating")
    text = await format_heating_status(callback.message.chat.id)
    now = datetime.now().strftime("%H:%M:%S")
    text += f"\n\n<i>Оновлено: {now}</i>"
    
    await callback.message.edit_text(text, reply_markup=get_heating_vote_keyboard(user_vote))


@router.callback_query(F.data == "menu_vote_heating_no")
async def cb_menu_vote_heating_no(callback: CallbackQuery):
    """Проголосувати: немає опалення (з меню, оновлює статус)."""
    from database import vote_heating, get_user_vote
    await vote_heating(callback.message.chat.id, False)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що опалення немає.", show_alert=True)
    
    user_vote = await get_user_vote(callback.message.chat.id, "heating")
    text = await format_heating_status(callback.message.chat.id)
    now = datetime.now().strftime("%H:%M:%S")
    text += f"\n\n<i>Оновлено: {now}</i>"
    
    await callback.message.edit_text(text, reply_markup=get_heating_vote_keyboard(user_vote))


@router.callback_query(F.data == "menu_vote_water_yes")
async def cb_menu_vote_water_yes(callback: CallbackQuery):
    """Проголосувати: є вода (з меню, оновлює статус)."""
    from database import vote_water, get_user_vote
    await vote_water(callback.message.chat.id, True)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що вода є.", show_alert=True)
    
    user_vote = await get_user_vote(callback.message.chat.id, "water")
    text = await format_water_status(callback.message.chat.id)
    now = datetime.now().strftime("%H:%M:%S")
    text += f"\n\n<i>Оновлено: {now}</i>"
    
    await callback.message.edit_text(text, reply_markup=get_water_vote_keyboard(user_vote))


@router.callback_query(F.data == "menu_vote_water_no")
async def cb_menu_vote_water_no(callback: CallbackQuery):
    """Проголосувати: немає води (з меню, оновлює статус)."""
    from database import vote_water, get_user_vote
    await vote_water(callback.message.chat.id, False)
    await safe_callback_answer(callback, "✅ Дякуємо за голос! Ви повідомили, що води немає.", show_alert=True)
    
    user_vote = await get_user_vote(callback.message.chat.id, "water")
    text = await format_water_status(callback.message.chat.id)
    now = datetime.now().strftime("%H:%M:%S")
    text += f"\n\n<i>Оновлено: {now}</i>"
    
    await callback.message.edit_text(text, reply_markup=get_water_vote_keyboard(user_vote))


# ============ Пошук закладів ============

# Стан для FSM пошуку
search_waiting_users = set()


LIGHT_KEYWORD = "світло"


def is_light_query(text: str) -> bool:
    """Перевіряє, чи містить запит слово 'світло' (у будь-якому оточенні)."""
    if not text:
        return False
    tokens = text.lower().split()
    return any(LIGHT_KEYWORD in token for token in tokens)


@router.message(F.text == "🔍 Пошук закладу")
async def reply_search(message: Message):
    """Обробник кнопки 'Пошук закладу' з ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    logger.info(f"User {format_user_label(message.from_user, message.chat.id)} clicked reply: Пошук закладу")
    try:
        await message.delete()
    except Exception:
        pass
    
    search_waiting_users.add(message.chat.id)
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=(
            "🔍 <b>Пошук закладів</b>\n\n"
            "Введіть назву, опис або ключове слово для пошуку.\n"
            "Наприклад: <i>сирники</i>, <i>кава</i>, <i>аптека</i>\n\n"
            "💡 Також можете шукати в будь-якому чаті через inline-режим:\n"
            f"<code>@{CFG.bot_username} сирники</code>\n\n"
            "⚡ Якщо напишете слово <b>світло</b>, бот покаже поточний статус електрики."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="menu")]]
        ),
        context_key="search_menu",
    )


@router.callback_query(F.data == "search_menu")
async def cb_search_menu(callback: CallbackQuery):
    """Показати меню пошуку."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Пошук закладу")
    search_waiting_users.add(callback.message.chat.id)
    await callback.message.edit_text(
        "🔍 <b>Пошук закладів</b>\n\n"
        "Введіть назву, опис або ключове слово для пошуку.\n"
        "Наприклад: <i>сирники</i>, <i>кава</i>, <i>аптека</i>\n\n"
        "💡 Також можете шукати в будь-якому чаті через inline-режим:\n"
        f"<code>@{CFG.bot_username} сирники</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Меню", callback_data="menu")],
        ])
    )
    await safe_callback_answer(callback)


async def do_search(query: str, user_id: int | None = None) -> str:
    """Виконати пошук та повернути форматований результат."""
    from database import search_places

    # Якщо запит містить 'світло' — показуємо статус світла і не шукаємо заклади
    if is_light_query(query):
        if user_id:
            text = await format_light_status(user_id, include_vote_prompt=False)
            return text
        else:
            # Inline режим не має user_id, тому не можемо визначити будинок/секцію.
            return (
                "💡 <b>Статус світла</b>\n\n"
                "Щоб побачити точну інформацію, відкрийте бота і оберіть будинок та секцію "
                "через «🏠 Обрати будинок»."
            )
    
    results = await search_places(query)
    
    if not results:
        return f"🔍 За запитом «<b>{query}</b>» нічого не знайдено."
    
    # Медалі для топ-3
    medals = ["🥇", "🥈", "🥉"]
    
    text = f"🔍 Результати пошуку «<b>{query}</b>»:\n\n"
    
    for i, p in enumerate(results):
        likes_count = p.get('likes_count', 0)
        
        # Медаль для топ-3 (тільки якщо є лайки)
        if i < 3 and likes_count > 0:
            medal = medals[i] + " "
        else:
            medal = ""
        
        # Текст лайків
        likes_text = f" ❤️{likes_count}" if likes_count > 0 else ""
        
        text += f"📍 <b>{medal}{p['name']}</b>{likes_text}\n"
        text += f"   📁 {p['service_name']}\n"
        if p['description']:
            text += f"   📝 {p['description']}\n"
        if p['address']:
            text += f"   🏠 {p['address']}\n"
        text += "\n"
    
    admin_tag = CFG.admin_tag or "адміністратору"
    text += f"💬 Побачили помилку? Пишіть {admin_tag}"
    
    return text


# Inline режим для пошуку
@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    """Inline пошук закладів."""
    query = inline_query.query.strip()
    
    if not query:
        # Показуємо підказку
        await inline_query.answer(
            results=[],
            switch_pm_text="🔍 Введіть запит для пошуку",
            switch_pm_parameter="search",
            cache_time=1
        )
        return
    
    # Special intents for adbot (services + light) are handled before catalog search.
    special = resolve_inline_special_result(query, cfg=CFG)
    if special is not None:
        articles = [
            InlineQueryResultArticle(
                id=special.result_id,
                title=special.title,
                description=special.description,
                input_message_content=InputTextMessageContent(
                    message_text=special.message_text,
                    parse_mode="HTML"
                ),
            )
        ]
        await inline_query.answer(results=articles, cache_time=5)
        return

    from database import search_places

    results = await search_places(query)
    
    # Медалі для топ-3
    medals = ["🥇", "🥈", "🥉"]
    
    articles = []
    for i, p in enumerate(results[:10]):  # Максимум 10 результатів
        description = p['description'] or ""
        address = p['address'] or ""
        likes_count = p.get('likes_count', 0)
        
        # Медаль для топ-3 (тільки якщо є лайки)
        if i < 3 and likes_count > 0:
            medal = medals[i] + " "
        else:
            medal = ""
        
        # Текст лайків
        likes_text = f" ❤️{likes_count}" if likes_count > 0 else ""
        
        text = f"📍 <b>{medal}{p['name']}</b>{likes_text}\n"
        text += f"📁 Категорія: {p['service_name']}\n"
        if description:
            text += f"📝 {description}\n"
        if address:
            text += f"🏠 {address}\n"
        
        # Перевіряємо чи є карта для цього закладу
        has_map = get_map_file_for_address(address) is not None
        
        # Заголовок з медаллю та лайками
        title = f"{medal}{p['name']}{likes_text}"
        
        # Якщо є карта - додаємо emoji карти в description
        desc_text = f"{p['service_name']} • {address}" if address else p['service_name']
        if has_map:
            desc_text = f"🗺️ {desc_text}"
            text += f"\n🗺️ <i>Натисніть кнопку нижче для перегляду карти</i>"
        
        # Кнопка для перегляду деталей з картою в боті
        bot_username = CFG.bot_username or "NaButlerBot"
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🗺️ Показати на карті" if has_map else "📍 Детальніше",
                url=f"https://t.me/{bot_username}?start=place_{p['id']}"
            )]
        ])
        
        articles.append(
            InlineQueryResultArticle(
                id=str(p['id']),
                title=title,
                description=desc_text,
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML"
                ),
                reply_markup=reply_markup
            )
        )
    
    if not articles:
        articles.append(
            InlineQueryResultArticle(
                id="0",
                title="Нічого не знайдено",
                description=f"За запитом «{query}» немає результатів",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔍 За запитом «{query}» нічого не знайдено."
                )
            )
        )
    
    await inline_query.answer(results=articles, cache_time=60)


@router.message(F.text.startswith("/adbot"))
async def handle_adbot_internal_command(message: Message):
    """Internal command for adbot pipeline fallback (admin-only)."""
    user_id = int(message.from_user.id) if message.from_user else 0
    chat_id = int(message.chat.id) if message.chat else 0
    is_admin = user_id > 0 and user_id in set(CFG.admin_ids)
    is_adbot_internal_chat = (
        ADBOT_INTERNAL_CHAT_ID is not None
        and chat_id != 0
        and int(ADBOT_INTERNAL_CHAT_ID) in _chat_id_variants(chat_id)
    )
    if not is_admin and not is_adbot_internal_chat:
        logger.info(
            "adbot_internal_command denied: user_id=%s chat_id=%s is_admin=%s is_internal=%s env_internal_chat_id=%s",
            user_id,
            chat_id,
            is_admin,
            is_adbot_internal_chat,
            ADBOT_INTERNAL_CHAT_ID,
        )
        return

    raw_text = str(message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    logger.info(
        "adbot_internal_command accepted: user_id=%s chat_id=%s query=%s",
        user_id,
        chat_id,
        query,
    )

    async def _safe_send(text: str) -> None:
        try:
            await message.reply(text)
            return
        except Exception as exc:
            logger.warning(
                "adbot_internal_command reply failed; fallback to answer: user_id=%s chat_id=%s err=%s",
                user_id,
                chat_id,
                exc,
            )
        await message.answer(text)

    if not query:
        await _safe_send("Використання: <code>/adbot &lt;запит&gt;</code>")
        return

    special = resolve_inline_special_result(query, cfg=CFG)
    if special is not None:
        await _safe_send(special.message_text)
        return

    # Fallback to shared resident search renderer for non-special intents.
    text = await do_search(query, user_id=user_id)
    await _safe_send(text)


# Обробка тегу бота в групі
@router.message(F.text.contains(f"@{CFG.bot_username}") if CFG.bot_username else F.text.regexp(r"^$"))
async def handle_bot_mention(message: Message):
    """Обробка згадки бота в групі для пошуку."""
    # Пропускаємо якщо це приватний чат
    if message.chat.type == "private":
        return
    
    # Видаляємо тег бота з тексту
    query = message.text.replace(f"@{CFG.bot_username}", "").strip()
    
    if not query:
        await message.reply(
            "🔍 Вкажіть що шукати після тегу бота.\n"
            f"Наприклад: <code>@{CFG.bot_username} сирники</code>"
        )
        return
    
    text = await do_search(query, user_id=message.from_user.id if message.from_user else None)
    await message.reply(text)


# Обробка текстових повідомлень для пошуку
@router.message(F.text & ~F.text.startswith("/"))
async def handle_search_query(message: Message):
    """Обробка пошукового запиту."""
    # Якщо це група - ігноруємо (якщо не тег)
    if message.chat.type != "private":
        return
    
    # Перевіряємо чи користувач в режимі пошуку
    if message.chat.id not in search_waiting_users:
        return
    
    search_waiting_users.discard(message.chat.id)
    
    try:
        await message.delete()
    except Exception:
        pass
    
    query = message.text.strip()
    text = await do_search(query, user_id=message.chat.id)
    
    await render_or_edit(
        bot=message.bot,
        chat_id=message.chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новий пошук", callback_data="search_menu")],
                [InlineKeyboardButton(text="« Меню", callback_data="menu")],
            ]
        ),
        context_key="search_menu",
    )
