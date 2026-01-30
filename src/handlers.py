from aiogram import Router, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, ReplyKeyboardRemove,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile, User
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict
import re

from config import CFG
from database import (
    add_subscriber, remove_subscriber, db_get, db_set, set_quiet_hours, get_quiet_hours,
    get_notification_settings, set_light_notifications, set_alert_notifications,
    get_last_event, get_subscriber_building, get_building_by_id, save_last_bot_message
)
from services import state_text, calculate_stats, format_duration, format_light_status

router = Router()
logger = logging.getLogger(__name__)


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
        await callback.answer()
    except Exception:
        pass


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
    menu_msg = await message.answer(
        f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=get_main_keyboard(),
    )
    await save_last_bot_message(message.chat.id, menu_msg.message_id)
    return True


# ============ FSM States для інтерактивного додавання закладу ============

class AddPlaceStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_address = State()
    waiting_for_keywords = State()


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
    """Показати заклад з картою (для deep link з inline режиму)."""
    from database import get_place, get_general_service, get_place_likes_count
    
    place = await get_place(place_id)
    if not place:
        await message.answer("❌ Заклад не знайдено.")
        return
    
    service = await get_general_service(place["service_id"])
    likes_count = await get_place_likes_count(place_id)
    admin_tag = CFG.admin_tag or "адміністратору"
    
    text = f"🏢 <b>{place['name']}</b>\n\n"
    
    if service:
        text += f"📁 Категорія: {service['name']}\n\n"
    
    if place["description"]:
        text += f"📝 {place['description']}\n\n"
    
    if place["address"]:
        text += f"📍 <b>Адреса:</b> {place['address']}\n\n"
    
    text += f"❤️ <b>Лайків:</b> {likes_count}\n\n"
    text += f"💬 Побачили помилку? Пишіть {admin_tag}"
    
    # Визначаємо карту
    map_file = get_map_file_for_address(place["address"])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Головне меню", callback_data="menu")],
    ])
    
    if map_file:
        photo = FSInputFile(map_file)
        await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=keyboard
        )
    else:
        await message.answer(text, reply_markup=keyboard)


async def get_user_building_text(user_id: int) -> str:
    """Отримати текст з назвою будинку користувача."""
    building_id = await get_subscriber_building(user_id)
    if building_id:
        building = get_building_by_id(building_id)
        if building:
            return f"🏢 Ваш будинок: {building['name']}"
    return "🏢 Будинок не обрано"


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
    from database import get_subscriber_building, get_sensors_by_building
    
    user_building_id = await get_subscriber_building(user_id)
    if not user_building_id:
        return "💡 Світло: оберіть будинок"
    
    # Перевіряємо чи є сенсори
    sensors = await get_sensors_by_building(user_building_id)
    if not sensors:
        return "💡 Світло: немає даних"
    
    # Рахуємо онлайн сенсори (онлайн = світло є)
    sensors_online = 0
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    for s in sensors:
        if s["last_heartbeat"] and (now - s["last_heartbeat"]) < timeout:
            sensors_online += 1
    
    # Світло є якщо хоча б один сенсор онлайн
    if sensors_online > 0:
        return "💡 Є світло"
    else:
        return "💡 Немає світла"
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
        [
            InlineKeyboardButton(text="☕ Подякувати розробнику", callback_data="donate"),
        ],
    ])


def get_service_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура сервісної служби з телефонами."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛡️ Охорона", callback_data="service_security"),
        ],
        [
            InlineKeyboardButton(text="🔧 Сантехнік", callback_data="service_plumber"),
        ],
        [
            InlineKeyboardButton(text="⚡ Електрик", callback_data="service_electrician"),
        ],
        [
            InlineKeyboardButton(text="🛗 Диспетчер ліфтів", callback_data="service_elevator"),
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
    
    light_status = "✅" if settings["light_notifications"] else "❌"
    alert_status = "✅" if settings["alert_notifications"] else "❌"
    
    # Формуємо текст для тихих годин
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        quiet_text = f"🌙 {settings['quiet_start']:02d}:00-{settings['quiet_end']:02d}:00"
    else:
        quiet_text = "🔔 Вимкнено"
    
    return InlineKeyboardMarkup(inline_keyboard=[
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
                text=f"⏰ Тихі години: {quiet_text}",
                callback_data="notif_quiet_hours"
            ),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])


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
    
    from database import get_subscriber_building, get_building_by_id
    
    building_id = await get_subscriber_building(message.chat.id)
    current_text = ""
    if building_id:
        building = get_building_by_id(building_id)
        if building:
            current_text = f"\n\n📍 Ваш поточний будинок: <b>{building['name']} ({building['address']})</b>"
    
    await message.answer(
        f"🏠 <b>Оберіть свій будинок</b>{current_text}\n\n"
        "Обравши будинок, ви будете отримувати сповіщення про світло саме по вашому будинку:",
        reply_markup=get_buildings_keyboard()
    )


@router.callback_query(F.data == "select_building")
async def cb_select_building(callback: CallbackQuery):
    """Показати меню вибору будинку."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Обрати будинок")
    from database import get_subscriber_building, get_building_by_id
    
    building_id = await get_subscriber_building(callback.message.chat.id)
    current_text = ""
    if building_id:
        building = get_building_by_id(building_id)
        if building:
            current_text = f"\n\n📍 Ваш поточний будинок: <b>{building['name']} ({building['address']})</b>"
    
    await callback.message.edit_text(
        f"🏠 <b>Оберіть свій будинок</b>{current_text}\n\n"
        "Обравши будинок, ви будете отримувати сповіщення про світло саме по вашому будинку:",
        reply_markup=get_buildings_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("building_"))
async def cb_building_selected(callback: CallbackQuery):
    """Обробка вибору будинку."""
    from database import (
        set_subscriber_building, get_building_by_id, 
        NEWCASTLE_BUILDING_ID, add_subscriber
    )
    
    building_id = int(callback.data.split("_")[1])
    building = get_building_by_id(building_id)
    
    if not building:
        await callback.answer("❌ Будинок не знайдено", show_alert=True)
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
    
    display_name = f"{building['name']} ({building['address']})"
    
    # Якщо це Ньюкасл - є сенсор
    if building_id == NEWCASTLE_BUILDING_ID:
        text = (
            f"✅ <b>Ви підписались на сповіщення по будинку {display_name}</b>\n\n"
            "Надалі ви будете отримувати сповіщення про відключення світла в цьому будинку."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☀️ Перевірити світло", callback_data="status")],
            [InlineKeyboardButton(text="« Меню", callback_data="menu")],
        ])
    else:
        # Інші будинки - сенсорів поки немає
        text = (
            f"🔌 Поки що сповіщення по будинку «{display_name}» недоступні.\n"
            "Але це тимчасово.\n\n"
            "Я розробляю компактний пристрій, який мешканці зможуть встановити у своєму будинку. "
            "Він дозволить точно визначати відключення електроенергії саме по вашому будинку, "
            "а не «в середньому по ЖК».\n\n"
            "У перспективі кожен будинок матиме 1–кілька таких пристроїв, що зробить систему максимально точною.\n"
            "💰 Вартість одного комплекту — близько 30 $. Пристрої збираю поступово — за рахунок донатів на розвиток проєкту.\n\n"
            "🤝 Долучитись можуть мешканці або бізнес ЖК «Нова Англія»:\n"
            "👉 https://send.monobank.ua/jar/7d56pmvjEB\n\n"
            "📝 У коментарі до платежу вкажіть назву будинку.\n"
            "Пристрої будуть передані мешканцям з найбільшим внеском по конкретному будинку.\n\n"
            "Разом зробимо систему, яка працює точно і для своїх."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Підтримати проєкт", url="https://send.monobank.ua/jar/7d56pmvjEB")],
            [InlineKeyboardButton(text="🏠 Обрати інший будинок", callback_data="select_building")],
            [InlineKeyboardButton(text="« Меню", callback_data="menu")],
        ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
    menu_msg = await message.answer(
        f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=get_main_keyboard()
    )
    await save_last_bot_message(message.chat.id, menu_msg.message_id)


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    """Показати головне меню з кнопками."""
    logger.info(f"User {message.chat.id} opened menu")
    # Показуємо InlineKeyboard
    building_text = await get_user_building_text(message.chat.id)
    light_status = await get_light_status_text(message.chat.id)
    alert_status = await get_alert_status_text()
    await remove_reply_keyboard(message)
    menu_msg = await message.answer(
        f"{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:",
        reply_markup=get_main_keyboard()
    )
    await save_last_bot_message(message.chat.id, menu_msg.message_id)


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
    
    stats = await calculate_stats(days)
    
    if stats['outage_count'] == 0:
        await message.answer(
            f"📊 <b>Статистика {period_text}</b>\n\n"
            "✨ Відключень не зафіксовано!\n"
            f"⚡ Uptime: 100%"
        )
        return
    
    response = (
        f"📊 <b>Статистика {period_text}</b>\n\n"
        f"⚡ Uptime: {stats['uptime_percent']:.1f}%\n"
        f"🔌 Кількість відключень: {stats['outage_count']}\n"
        f"⏱ Загальний час без світла: {format_duration(stats['total_downtime'])}\n"
    )
    
    if stats['outage_count'] > 0:
        avg_outage = stats['total_downtime'] / stats['outage_count']
        response += f"📉 Середня тривалість: {format_duration(avg_outage)}\n"
    
    response += f"\n<i>Період: {stats['period_start'].strftime('%d.%m.%Y %H:%M')} — {stats['period_end'].strftime('%d.%m.%Y %H:%M')}</i>"
    
    await message.answer(response)


# ============ Callback handlers (Inline-кнопки) ============

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    """Показати головне меню."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Головне меню")
    building_text = await get_user_building_text(callback.from_user.id)
    light_status = await get_light_status_text(callback.message.chat.id)
    alert_status = await get_alert_status_text()
    text = f"🏠 <b>Головне меню</b>\n{building_text}\n{light_status}\n{alert_status}\n\nОберіть дію:"
    
    # Якщо повідомлення має фото - видаляємо і відправляємо нове
    menu_msg = None
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        menu_msg = await callback.message.answer(text, reply_markup=get_main_keyboard())
    else:
        try:
            await callback.message.edit_text(text, reply_markup=get_main_keyboard())
            menu_msg = callback.message
        except Exception:
            # Якщо не вдалось редагувати - надсилаємо нове
            menu_msg = await callback.message.answer(text, reply_markup=get_main_keyboard())
    if menu_msg:
        await save_last_bot_message(callback.message.chat.id, menu_msg.message_id)
    await callback.answer()


@router.callback_query(F.data == "utilities_menu")
async def cb_utilities_menu(callback: CallbackQuery):
    """Показати меню Світло/Опалення/Вода."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Світло/опалення/вода")
    text = "💡 <b>Світло / Опалення / Вода</b>\n\nОберіть розділ:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="☀️ Світло", callback_data="status"),
        ],
        [
            InlineKeyboardButton(text="♨️ Опалення", callback_data="heating_menu"),
        ],
        [
            InlineKeyboardButton(text="💧 Вода", callback_data="water_menu"),
        ],
        [
            InlineKeyboardButton(text="📈 Статистика", callback_data="stats"),
        ],
        [
            InlineKeyboardButton(text="🗓 Орієнтовні графіки", callback_data="yasno_schedule"),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass  # Якщо повідомлення не змінилось - ігноруємо
    await callback.answer()


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
        await callback.message.answer(text, reply_markup=keyboard)
    else:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
        await callback.answer("Укриття не знайдено", show_alert=True)
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
    
    await callback.answer()


@router.callback_query(F.data.startswith("shelter_like_"))
async def cb_like_shelter(callback: CallbackQuery):
    """Поставити лайк укриттю."""
    from database import like_shelter, get_shelter_likes_count
    
    shelter_id = int(callback.data.split("_")[2])
    added = await like_shelter(shelter_id, callback.from_user.id)
    
    if added:
        likes_count = await get_shelter_likes_count(shelter_id)
        await callback.answer(f"❤️ Дякуємо за лайк! Усього: {likes_count}")
    else:
        await callback.answer("Ви вже лайкнули це укриття")
    
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
        await callback.answer(f"💔 Лайк забрано. Усього: {likes_count}")
    else:
        await callback.answer("Ви не лайкали це укриття")
    
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
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Оновити", callback_data="status")],
            [InlineKeyboardButton(text="🗓 Орієнтовні графіки", callback_data="yasno_schedule")],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "yasno_schedule")
async def cb_yasno_schedule(callback: CallbackQuery):
    """Показати орієнтовні графіки відключень."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Орієнтовні графіки")
    from database import get_subscriber_building
    from yasno import get_building_schedule_text

    building_id = await get_subscriber_building(callback.message.chat.id)
    text = await get_building_schedule_text(building_id) if building_id else "⚠️ Спочатку оберіть будинок."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


async def format_stats_message(days: int | None, period_text: str) -> str:
    """Форматувати повідомлення зі статистикою."""
    stats = await calculate_stats(days)
    
    if stats['outage_count'] == 0:
        return (
            f"📊 <b>Статистика {period_text}</b>\n\n"
            "✨ Відключень не зафіксовано!\n"
            "⚡ Uptime: 100%"
        )
    
    response = (
        f"📊 <b>Статистика {period_text}</b>\n\n"
        f"⚡ Uptime: {stats['uptime_percent']:.1f}%\n"
        f"🔌 Кількість відключень: {stats['outage_count']}\n"
        f"⏱ Загальний час без світла: {format_duration(stats['total_downtime'])}\n"
    )
    
    if stats['outage_count'] > 0:
        avg_outage = stats['total_downtime'] / stats['outage_count']
        response += f"📉 Середня тривалість: {format_duration(avg_outage)}\n"
    
    response += f"\n<i>Період: {stats['period_start'].strftime('%d.%m.%Y %H:%M')} — {stats['period_end'].strftime('%d.%m.%Y %H:%M')}</i>"
    
    return response


@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):
    """Показати статистику за весь час."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (весь час)")
    text = await format_stats_message(None, "за весь час")
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
    await callback.answer()


@router.callback_query(F.data == "stats_day")
async def cb_stats_day(callback: CallbackQuery):
    """Показати статистику за день."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (день)")
    text = await format_stats_message(1, "за останню добу")
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
    await callback.answer()


@router.callback_query(F.data == "stats_week")
async def cb_stats_week(callback: CallbackQuery):
    """Показати статистику за тиждень."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (тиждень)")
    text = await format_stats_message(7, "за останній тиждень")
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
    await callback.answer()


@router.callback_query(F.data == "stats_month")
async def cb_stats_month(callback: CallbackQuery):
    """Показати статистику за місяць."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Статистика (місяць)")
    text = await format_stats_message(30, "за останній місяць")
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
    await callback.answer()


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
    )
    
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        text += f"\n⏰ <b>Тихі години:</b> {settings['quiet_start']:02d}:00 - {settings['quiet_end']:02d}:00"
    else:
        text += "\n⏰ <b>Тихі години:</b> вимкнено"
    
    await callback.message.edit_text(
        text,
        reply_markup=await get_notifications_keyboard(chat_id)
    )
    await callback.answer()


@router.callback_query(F.data == "notif_toggle_light")
async def cb_toggle_light_notifications(callback: CallbackQuery):
    """Переключити сповіщення про світло."""
    chat_id = callback.message.chat.id
    settings = await get_notification_settings(chat_id)
    
    new_value = not settings["light_notifications"]
    await set_light_notifications(chat_id, new_value)
    
    status = "увімкнено ✅" if new_value else "вимкнено ❌"
    await callback.answer(f"☀️ Сповіщення про світло {status}")
    
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
    await callback.answer(f"🚨 Сповіщення про тривоги {status}")
    
    # Оновлюємо меню
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
    await callback.answer()


@router.callback_query(F.data == "quiet_info")
async def cb_quiet_info(callback: CallbackQuery):
    """Показати інформацію про тихі години (редирект на нове меню)."""
    await cb_notifications_menu(callback)


@router.callback_query(F.data == "donate")
async def cb_donate(callback: CallbackQuery):
    """Показати інформацію про підтримку розробника."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Подякувати розробнику")
    text = (
        "☕ <b>Подякувати розробнику</b>\n\n"
        "Цей бот — некомерційний проєкт, створений для зручності мешканців ЖК.\n\n"
        "Якщо він вам корисний і ви хочете підтримати його розвиток — "
        "можете пригостити розробника кавою ☕\n\n"
        "Ваші донати допомагають мені покривати:\n"
        "• 💻 Обслуговування сервера\n"
        "• 🌐 Закупівлю нових сенсорів\n"
        "• ⚡ Розвиток нових функцій\n\n"
        "Дякую за підтримку! 🙏"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Підтримати (Monobank)", url="https://send.monobank.ua/jar/7d56pmvjEB")],
            [InlineKeyboardButton(text="« Назад", callback_data="menu")],
        ])
    )
    await callback.answer()


@router.message(F.text == "☕ Подякувати розробнику")
async def reply_donate(message: Message):
    """Обробник кнопки подяки на ReplyKeyboard."""
    if await handle_webapp_reply_keyboard(message):
        return
    text = (
        "☕ <b>Подякувати розробнику</b>\n\n"
        "Цей бот — некомерційний проєкт, створений для зручності мешканців ЖК.\n\n"
        "Якщо він вам корисний і ви хочете підтримати його розвиток — "
        "можете пригостити розробника кавою ☕\n\n"
        "Ваші донати допомагають покривати:\n"
        "• 💻 Оренду сервера\n"
        "• 🌐 Оплату статичної IP-адреси\n"
        "• ⚡ Розвиток нових функцій\n\n"
        "Дякую за підтримку! 🙏"
    )
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Підтримати (Monobank)", url="https://send.monobank.ua/jar/7d56pmvjEB")],
            [InlineKeyboardButton(text="« Меню", callback_data="menu")],
        ])
    )


@router.callback_query(F.data.startswith("quiet_"))
async def cb_quiet_set(callback: CallbackQuery):
    """Встановити тихі години."""
    chat_id = callback.message.chat.id
    data = callback.data
    
    if data == "quiet_off":
        await set_quiet_hours(chat_id, None, None)
        await callback.answer("🔔 Тихі години вимкнено")
    else:
        # Парсимо quiet_23_7 -> start=23, end=7
        parts = data.replace("quiet_", "").split("_")
        if len(parts) == 2:
            start, end = int(parts[0]), int(parts[1])
            await set_quiet_hours(chat_id, start, end)
            await callback.answer(f"🌙 Тихі години: {start:02d}:00 - {end:02d}:00")
        else:
            await callback.answer("Помилка")
            return
    
    # Повертаємось до меню сповіщень
    await cb_notifications_menu(callback)


# ============ Адмін-команди ============

def is_admin(user_id: int) -> bool:
    """Перевірити чи є користувач адміном."""
    return user_id in CFG.admin_ids


async def _get_admin_panel_content():
    """Генерує текст і клавіатуру для адмін-панелі."""
    from database import db_get, get_all_active_sensors
    from config import CFG
    
    light_notifications = await db_get("light_notifications_global")
    light_status = "🟢 Увімкнені" if light_notifications != "off" else "🔴 Вимкнені"
    
    sensors = await get_all_active_sensors()
    sensors_count = len(sensors)
    sensors_online = 0
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    
    for s in sensors:
        if s["last_heartbeat"] and (now - s["last_heartbeat"]) < timeout:
            sensors_online += 1
    
    text = (
        "🔧 <b>Адмін-панель</b>\n\n"
        f"💡 <b>Сповіщення про світло:</b> {light_status}\n"
        f"📡 <b>Сенсори:</b> {sensors_online}/{sensors_count} онлайн\n\n"
        "📋 <b>Команди керування:</b>\n\n"
        "<b>Сповіщення:</b>\n"
        "• /light_notify on|off — увімкнути/вимкнути сповіщення про світло\n\n"
        "<b>Розсилка:</b>\n"
        "• /broadcast [текст] — надіслати повідомлення всім\n\n"
        "<b>Статистика:</b>\n"
        "• /subscribers — кількість підписників\n"
        "• /sensors — статус ESP32 сенсорів\n\n"
        "<b>Контент:</b>\n"
        "• /add_general_service [назва] — додати категорію\n"
        "  <i>Приклад:</i> <code>/add_general_service Кав'ярні</code>\n"
        "• /add_place — додати заклад (інтерактивно)\n"
        "• /list_places — список всіх закладів\n"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔴 Вимкнути сповіщення" if light_notifications != "off" else "🟢 Увімкнути сповіщення",
                callback_data="admin_toggle_light_notify"
            )
        ],
        [
            InlineKeyboardButton(text="📡 Статус сенсорів", callback_data="admin_sensors_status"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика підписників", callback_data="admin_subscribers_stats"),
        ],
        [
            InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu"),
        ],
    ])
    
    return text, keyboard


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """
    Адмін-панель з усіма командами керування.
    """
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    text, keyboard = await _get_admin_panel_content()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "admin_toggle_light_notify")
async def cb_admin_toggle_light_notify(callback: CallbackQuery):
    """Перемикання глобальних сповіщень про світло."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адмінів", show_alert=True)
        return
    
    from database import db_get, db_set
    
    current = await db_get("light_notifications_global")
    if current == "off":
        await db_set("light_notifications_global", "on")
        await callback.answer("✅ Сповіщення увімкнено")
    else:
        await db_set("light_notifications_global", "off")
        await callback.answer("✅ Сповіщення вимкнено")
    
    # Оновлюємо панель
    text, keyboard = await _get_admin_panel_content()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass


@router.callback_query(F.data == "admin_sensors_status")
async def cb_admin_sensors_status(callback: CallbackQuery):
    """Показати статус всіх сенсорів."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адмінів", show_alert=True)
        return
    
    from database import get_all_active_sensors, get_building_by_id
    from config import CFG
    
    sensors = await get_all_active_sensors()
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    
    if not sensors:
        text = "📡 <b>Сенсори</b>\n\nНемає зареєстрованих сенсорів."
    else:
        text = "📡 <b>Статус сенсорів</b>\n\n"
        for sensor in sensors:
            building = get_building_by_id(sensor["building_id"])
            building_name = building["name"] if building else f"ID:{sensor['building_id']}"
            
            if sensor["last_heartbeat"]:
                time_ago = now - sensor["last_heartbeat"]
                is_online = time_ago < timeout
                status = "🟢" if is_online else "🔴"
                
                # Форматуємо час
                if time_ago.total_seconds() < 60:
                    time_str = f"{int(time_ago.total_seconds())} сек тому"
                elif time_ago.total_seconds() < 3600:
                    time_str = f"{int(time_ago.total_seconds() // 60)} хв тому"
                else:
                    time_str = f"{int(time_ago.total_seconds() // 3600)} год тому"
            else:
                status = "⚪"
                time_str = "ніколи"
            
            sensor_name = sensor["name"] or sensor["uuid"][:12]
            text += f"{status} <b>{building_name}</b>: {sensor_name}\n"
            text += f"    Останній heartbeat: {time_str}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_subscribers_stats")
async def cb_admin_subscribers_stats(callback: CallbackQuery):
    """Показати статистику підписників по будинках."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адмінів", show_alert=True)
        return
    
    from database import count_subscribers, get_subscribers_by_building, get_building_by_id
    
    total = await count_subscribers()
    
    text = f"📊 <b>Статистика підписників</b>\n\n"
    text += f"<b>Всього:</b> {total}\n\n"
    text += "<b>По будинках:</b>\n"
    
    building_stats = await get_subscribers_by_building()
    for building_id, count in sorted(building_stats.items(), key=lambda x: -x[1]):
        if building_id is None:
            continue
        building = get_building_by_id(building_id)
        if building:
            text += f"• {building['name']}: {count}\n"
    
    # Без будинку
    no_building = building_stats.get(None, 0)
    if no_building:
        text += f"• Без будинку: {no_building}\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    """Повернутися до адмін-панелі."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адмінів", show_alert=True)
        return
    
    text, keyboard = await _get_admin_panel_content()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("sensors"))
async def cmd_sensors(message: Message):
    """Показати статус сенсорів (для адмінів)."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import get_all_active_sensors, get_building_by_id
    from config import CFG
    
    sensors = await get_all_active_sensors()
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    
    if not sensors:
        await message.answer("📡 <b>Сенсори</b>\n\nНемає зареєстрованих сенсорів.")
        return
    
    text = "📡 <b>Статус ESP32 сенсорів</b>\n\n"
    for sensor in sensors:
        building = get_building_by_id(sensor["building_id"])
        building_name = building["name"] if building else f"ID:{sensor['building_id']}"
        
        if sensor["last_heartbeat"]:
            time_ago = now - sensor["last_heartbeat"]
            is_online = time_ago < timeout
            status = "🟢 онлайн" if is_online else "🔴 офлайн"
            
            if time_ago.total_seconds() < 60:
                time_str = f"{int(time_ago.total_seconds())} сек тому"
            elif time_ago.total_seconds() < 3600:
                time_str = f"{int(time_ago.total_seconds() // 60)} хв тому"
            else:
                time_str = sensor["last_heartbeat"].strftime("%d.%m %H:%M")
        else:
            status = "⚪ невідомо"
            time_str = "ніколи"
        
        sensor_name = sensor["name"] or sensor["uuid"]
        text += f"<b>{building_name}</b>\n"
        text += f"  UUID: <code>{sensor['uuid']}</code>\n"
        text += f"  Статус: {status}\n"
        text += f"  Heartbeat: {time_str}\n\n"
    
    await message.answer(text)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    """
    Надіслати повідомлення всім підписникам.
    Тільки для адміністраторів.
    Формат: /broadcast Ваше повідомлення
    """
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    # Отримуємо текст після команди
    text = message.text.replace("/broadcast", "", 1).strip() if message.text else ""
    
    if not text:
        await message.answer(
            "📢 <b>Розсилка</b>\n\n"
            "Формат: <code>/broadcast Ваше повідомлення</code>\n\n"
            "Повідомлення буде надіслано всім підписникам."
        )
        return
    
    from database import list_subscribers
    
    subscribers = await list_subscribers()
    sent = 0
    failed = 0
    
    for chat_id in subscribers:
        try:
            await message.bot.send_message(chat_id, f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # 40ms затримка = 25 msg/sec (захист від rate limit)
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import list_subscribers
    
    subscribers = await list_subscribers()
    
    text = (
        f"🔧 <b>Адмін-панель</b>\n\n"
        f"👥 Підписників: {len(subscribers)}\n\n"
        
        f"<b>📢 Розсилка та управління:</b>\n"
        f"<code>/broadcast текст</code> — розіслати всім\n"
        f"<code>/subscribers</code> — список підписників\n"
        f"<code>/myid</code> — дізнатися свій ID\n\n"
        f"<code>/light_notify on/off</code> — глобально увімкнути/вимкнути сповіщення про світло\n\n"
        
        f"<b>📁 Категорії послуг:</b>\n"
        f"<code>/show_general_services</code> — всі категорії з ID\n"
        f"<code>/add_general_service Назва</code> — додати\n"
        f"<code>/edit_general_service ID Нова назва</code> — редагувати\n"
        f"<code>/delete_general_service ID</code> — видалити\n\n"
        
        f"<b>🏢 Заклади:</b>\n"
        f"<code>/list_places</code> — всі заклади з ID\n"
        f"<code>/add_place ID;Назва;Опис;Адреса;ключові,слова</code>\n"
        f"<code>/edit_place PlaceID ID;Назва;Опис;Адреса;ключові,слова</code>\n"
        f"<code>/set_keywords PlaceID ключ1,ключ2,ключ3</code>\n"
        f"<code>/delete_place PlaceID</code>\n\n"
        
        f"<b>📍 Формат адреси для карти:</b>\n"
        f"<code>Брістоль (24-б), зі сторони Бермінгема</code>\n"
        f"<code>Манчестер (26-г), зі сторони Брайтона, -1 поверх</code>"
    )
    
    await message.answer(text)


@router.message(Command("light_notify"))
async def cmd_light_notify(message: Message):
    """Увімкнути/вимкнути глобальні сповіщення про світло. Тільки для адміністраторів."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return

    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        enabled = (await db_get("light_notifications_global")) != "off"
        status = "увімкнені" if enabled else "вимкнені"
        await message.answer(
            f"☀️ Глобальні сповіщення про світло зараз {status}.\n"
            "Використання: <code>/light_notify on</code> або <code>/light_notify off</code>"
        )
        return

    value = parts[1].lower()
    if value in {"on", "enable", "1"}:
        await db_set("light_notifications_global", "on")
        await message.answer("✅ Глобальні сповіщення про світло увімкнено.")
    elif value in {"off", "disable", "0"}:
        await db_set("light_notifications_global", "off")
        await message.answer("⏸ Глобальні сповіщення про світло вимкнено.")
    else:
        await message.answer(
            "❌ Невірний параметр. Використання: <code>/light_notify on</code> або <code>/light_notify off</code>"
        )


@router.message(Command("subscribers"))
async def cmd_subscribers(message: Message):
    """Показати кількість підписників та надіслати файл зі списком (тільки для адмінів)."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import list_subscribers_full
    import io
    from aiogram.types import BufferedInputFile
    
    subscribers = await list_subscribers_full()
    
    if not subscribers:
        await message.answer("👥 Підписників немає.")
        return
    
    count = len(subscribers)
    
    # Формуємо текстовий файл зі списком
    lines = [f"Підписники бота ({count} осіб)", "=" * 40, ""]
    
    for i, sub in enumerate(subscribers, 1):
        name = sub["first_name"] or "—"
        username = f"@{sub['username']}" if sub["username"] else "—"
        chat_id = sub.get("chat_id", "—")
        
        # Дата підписки
        subscribed = ""
        if sub["subscribed_at"]:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(sub["subscribed_at"])
                subscribed = dt.strftime("%d.%m.%Y %H:%M")
            except:
                subscribed = sub["subscribed_at"]
        
        lines.append(f"{i}. {name} | {username} | ID: {chat_id} | {subscribed}")
    
    # Створюємо файл у пам'яті
    file_content = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(file_content, filename="subscribers.txt")
    
    await message.answer_document(
        file,
        caption=f"👥 <b>Підписники: {count}</b>\n\nПовний список у файлі."
    )


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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="☀️ Світло", callback_data="status"),
        ],
        [
            InlineKeyboardButton(text="♨️ Опалення", callback_data="heating_menu"),
        ],
        [
            InlineKeyboardButton(text="💧 Вода", callback_data="water_menu"),
        ],
        [
            InlineKeyboardButton(text="📈 Статистика", callback_data="stats"),
        ],
        [
            InlineKeyboardButton(text="🗓 Орієнтовні графіки", callback_data="yasno_schedule"),
        ],
        [
            InlineKeyboardButton(text="« Меню", callback_data="menu"),
        ],
    ])
    await message.answer(text, reply_markup=keyboard)


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
    await message.answer(text, reply_markup=keyboard)


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
    )
    
    if settings["quiet_start"] is not None and settings["quiet_end"] is not None:
        text += f"\n⏰ <b>Тихі години:</b> {settings['quiet_start']:02d}:00 - {settings['quiet_end']:02d}:00"
    else:
        text += "\n⏰ <b>Тихі години:</b> вимкнено"
    
    await message.answer(text, reply_markup=await get_notifications_keyboard(chat_id))


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
    "☕ Подякувати розробнику",
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
    r"Сповіщення та тихі години|Сповіщення|Тривоги та укриття|Подякувати розробнику"
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
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Оновити", callback_data="status")],
            [InlineKeyboardButton(text="« Назад", callback_data="utilities_menu")],
        ]),
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
    await message.answer(text, reply_markup=get_heating_vote_keyboard(user_vote))


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
    await message.answer(text, reply_markup=get_water_vote_keyboard(user_vote))


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
    await message.answer(
        "🔍 <b>Пошук закладу</b>\n\nВведіть назву або ключове слово для пошуку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Меню", callback_data="menu")]
        ])
    )


@router.message(F.text.in_(LEGACY_REPLY_TEXTS))
async def reply_keyboard_fallback(message: Message):
    """Фолбек: якщо прийшов текст з ReplyKeyboard у режимі WebApp — прибираємо клавіатуру."""
    if await handle_webapp_reply_keyboard(message):
        return


@router.message(F.text)
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
    
    await message.answer(
        "📞 <b>Цілодобова сервісна служба</b>\n\n"
        "Оберіть службу для отримання контактного телефону:",
        reply_markup=get_service_keyboard()
    )


# ============ Callback-обробники сервісної служби ============

@router.callback_query(F.data == "service_menu")
async def cb_service_menu(callback: CallbackQuery):
    """Показати меню сервісної служби."""
    logger.info(f"User {format_user_label(callback.from_user)} clicked: Сервісна служба")
    await callback.message.edit_text(
        "📞 <b>Цілодобова сервісна служба</b>\n\n"
        "Оберіть службу для отримання контактного телефону:",
        reply_markup=get_service_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "service_security")
async def cb_service_security(callback: CallbackQuery):
    """Показати телефон охорони."""
    phone = CFG.security_phone or "не вказано"
    await callback.message.edit_text(
        "🛡️ <b>Охорона</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "service_plumber")
async def cb_service_plumber(callback: CallbackQuery):
    """Показати телефон сантехніка."""
    phone = CFG.plumber_phone or "не вказано"
    await callback.message.edit_text(
        "🔧 <b>Черговий сантехнік</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "service_electrician")
async def cb_service_electrician(callback: CallbackQuery):
    """Показати телефон електрика."""
    phone = CFG.electrician_phone or "не вказано"
    await callback.message.edit_text(
        "⚡ <b>Черговий електрик</b>\n\n"
        f"📞 Телефон: <code>{phone}</code>\n\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "service_elevator")
async def cb_service_elevator(callback: CallbackQuery):
    """Показати телефон диспетчера ліфтів."""
    phones = CFG.elevator_phones or "не вказано"
    # Форматуємо телефони якщо їх кілька
    phone_lines = "".join([f"• <code>{p.strip()}</code>\n" for p in phones.split(",")]) if "," in phones else f"<code>{phones}</code>"
    await callback.message.edit_text(
        "🛗 <b>Диспетчер ліфтів</b>\n\n"
        f"📞 Телефони:\n{phone_lines}\n"
        "Працює цілодобово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="service_menu")],
        ])
    )
    await callback.answer()


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
    await callback.answer()


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
    await callback.answer()


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
        await message.answer(
            "🏢 <b>Заклади в ЖК</b>\n\n"
            "Поки що категорій немає.\n\n"
            f"💬 Хочете додати категорію? Пишіть {admin_tag}",
        )
        return
    
    admin_tag = CFG.admin_tag or "адміністратору"
    await message.answer(
        "🏢 <b>Заклади в ЖК</b>\n\n"
        f"Оберіть категорію:\n\n"
        f"💬 Хочете додати категорію? Пишіть {admin_tag}",
        reply_markup=await get_places_keyboard()
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
        await callback.answer()
        return
    
    admin_tag = CFG.admin_tag or "адміністратору"
    await callback.message.edit_text(
        "🏢 <b>Заклади в ЖК</b>\n\n"
        f"Оберіть категорію:\n\n"
        f"💬 Хочете додати категорію? Пишіть {admin_tag}",
        reply_markup=await get_places_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("places_cat_"))
async def cb_places_category(callback: CallbackQuery):
    """Показати заклади певної категорії."""
    from database import get_general_service, get_places_by_service_with_likes
    
    service_id = int(callback.data.split("_")[2])
    service = await get_general_service(service_id)
    
    if not service:
        await callback.answer("Категорію не знайдено", show_alert=True)
        return
    
    places = await get_places_by_service_with_likes(service_id)
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
        await callback.answer()
        return
    
    # Медалі для топ-3
    medals = ["🥇", "🥈", "🥉"]
    
    # Показуємо кнопки з закладами
    buttons = []
    for i, place in enumerate(places):
        # Додаємо медаль для топ-3 (тільки якщо є лайки)
        if i < 3 and place["likes_count"] > 0:
            prefix = medals[i] + " "
        else:
            prefix = ""
        
        # Показуємо кількість лайків
        likes_text = f" ❤️{place['likes_count']}" if place["likes_count"] > 0 else ""
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix}{place['name']}{likes_text}",
                callback_data=f"place_{place['id']}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="places_menu")])
    
    text = (
        f"🏢 <b>{service['name']}</b>\n\n"
        f"Оберіть заклад (❤️ = лайки мешканців):\n\n"
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
    
    await callback.answer()


@router.callback_query(F.data.startswith("place_"))
async def cb_place_detail(callback: CallbackQuery):
    """Показати інформацію про заклад з картою."""
    from database import get_place, get_general_service, has_liked_place, get_place_likes_count
    
    place_id = int(callback.data.split("_")[1])
    place = await get_place(place_id)
    
    if not place:
        await callback.answer("Заклад не знайдено", show_alert=True)
        return
    
    service = await get_general_service(place["service_id"])
    admin_tag = CFG.admin_tag or "адміністратору"
    
    # Перевіряємо чи користувач лайкнув
    user_liked = await has_liked_place(place_id, callback.from_user.id)
    likes_count = await get_place_likes_count(place_id)
    
    text = f"🏢 <b>{place['name']}</b>\n\n"
    
    if place["description"]:
        text += f"📝 {place['description']}\n\n"
    
    if place["address"]:
        text += f"📍 <b>Адреса:</b> {place['address']}\n\n"
    
    text += f"❤️ <b>Лайків:</b> {likes_count}\n\n"
    text += f"💬 Побачили помилку? Хочете додати детальніший опис? Пишіть {admin_tag}"
    
    # Визначаємо карту за будинком з адреси
    map_file = get_map_file_for_address(place["address"])
    
    # Кнопка лайку
    if user_liked:
        like_btn = InlineKeyboardButton(text=f"💔 Забрати лайк ({likes_count})", callback_data=f"unlike_{place_id}")
    else:
        like_btn = InlineKeyboardButton(text=f"❤️ Подобається ({likes_count})", callback_data=f"like_{place_id}")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [like_btn],
        [InlineKeyboardButton(text="« Назад", callback_data=f"places_cat_{place['service_id']}")],
    ])
    
    if map_file:
        # Видаляємо старе повідомлення і відправляємо фото з підписом
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
        # Якщо карти немає - просто текст
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("like_"))
async def cb_like_place(callback: CallbackQuery):
    """Поставити лайк закладу."""
    from database import like_place, get_place_likes_count, get_place
    
    place_id = int(callback.data.split("_")[1])
    
    # Ставимо лайк
    added = await like_place(place_id, callback.from_user.id)
    
    if added:
        likes_count = await get_place_likes_count(place_id)
        await callback.answer(f"❤️ Дякуємо за лайк! Усього: {likes_count}")
    else:
        await callback.answer("Ви вже лайкнули цей заклад")
    
    # Оновлюємо кнопку
    place = await get_place(place_id)
    if place:
        likes_count = await get_place_likes_count(place_id)
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💔 Забрати лайк ({likes_count})", callback_data=f"unlike_{place_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"places_cat_{place['service_id']}")],
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


@router.callback_query(F.data.startswith("unlike_"))
async def cb_unlike_place(callback: CallbackQuery):
    """Забрати лайк із закладу."""
    from database import unlike_place, get_place_likes_count, get_place
    
    place_id = int(callback.data.split("_")[1])
    
    # Забираємо лайк
    removed = await unlike_place(place_id, callback.from_user.id)
    
    if removed:
        likes_count = await get_place_likes_count(place_id)
        await callback.answer(f"💔 Лайк забрано. Усього: {likes_count}")
    else:
        await callback.answer("Ви не лайкали цей заклад")
    
    # Оновлюємо кнопку
    place = await get_place(place_id)
    if place:
        likes_count = await get_place_likes_count(place_id)
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"❤️ Подобається ({likes_count})", callback_data=f"like_{place_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"places_cat_{place['service_id']}")],
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


# ============ Адмін-команди для керування закладами ============

@router.message(Command("add_general_service"))
async def cmd_add_general_service(message: Message):
    """Додати категорію послуг."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/add_general_service Назва</code>\n"
            "Приклад: <code>/add_general_service Кафе</code>"
        )
        return
    
    name = args[1].strip()
    
    from database import add_general_service
    try:
        service_id = await add_general_service(name)
        await message.answer(f"✅ Категорію <b>{name}</b> додано (ID: {service_id})")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")


@router.message(Command("edit_general_service"))
async def cmd_edit_general_service(message: Message):
    """Редагувати категорію послуг."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/edit_general_service ІД Назва</code>\n"
            "Приклад: <code>/edit_general_service 1 Ресторани</code>"
        )
        return
    
    try:
        service_id = int(args[1])
    except ValueError:
        await message.answer("❌ ІД має бути числом.")
        return
    
    name = args[2].strip()
    
    from database import edit_general_service
    if await edit_general_service(service_id, name):
        await message.answer(f"✅ Категорію ID={service_id} оновлено на <b>{name}</b>")
    else:
        await message.answer(f"❌ Категорію з ID={service_id} не знайдено.")


@router.message(Command("delete_general_service"))
async def cmd_delete_general_service(message: Message):
    """Видалити категорію послуг."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/delete_general_service ІД</code>\n"
            "Приклад: <code>/delete_general_service 1</code>"
        )
        return
    
    try:
        service_id = int(args[1])
    except ValueError:
        await message.answer("❌ ІД має бути числом.")
        return
    
    from database import delete_general_service
    if await delete_general_service(service_id):
        await message.answer(f"✅ Категорію ID={service_id} та всі її заклади видалено.")
    else:
        await message.answer(f"❌ Категорію з ID={service_id} не знайдено.")


@router.message(Command("show_general_services"))
async def cmd_show_general_services(message: Message):
    """Показати всі категорії послуг."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import get_all_general_services
    
    services = await get_all_general_services()
    
    if not services:
        await message.answer("📋 Категорій немає.")
        return
    
    lines = ["📋 <b>Категорії послуг:</b>\n"]
    for s in services:
        lines.append(f"• ID={s['id']}: {s['name']}")
    
    await message.answer("\n".join(lines))


@router.message(Command("add_place"))
async def cmd_add_place(message: Message, state: FSMContext):
    """Інтерактивне додавання закладу."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import get_all_general_services
    
    services = await get_all_general_services()
    
    if not services:
        await message.answer("❌ Спочатку додайте категорії через /add_general_service")
        return
    
    # Створюємо кнопки для вибору категорії
    buttons = []
    for s in services:
        buttons.append([InlineKeyboardButton(
            text=s["name"],
            callback_data=f"addplace_cat_{s['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="addplace_cancel")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        "🏢 <b>Додавання закладу</b>\n\n"
        "<b>Крок 1/5:</b> Оберіть категорію:",
        reply_markup=keyboard
    )
    await state.set_state(AddPlaceStates.waiting_for_category)


@router.callback_query(F.data.startswith("addplace_cat_"))
async def process_category_selection(callback: CallbackQuery, state: FSMContext):
    """Обробка вибору категорії."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адміністраторів", show_alert=True)
        return
    
    service_id = int(callback.data.split("_")[2])
    
    from database import get_general_service
    service = await get_general_service(service_id)
    
    if not service:
        await callback.answer("❌ Категорію не знайдено", show_alert=True)
        return
    
    await state.update_data(service_id=service_id, service_name=service["name"])
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="addplace_cancel")]
    ])
    
    await callback.message.edit_text(
        f"🏢 <b>Додавання закладу</b>\n\n"
        f"✅ Категорія: <b>{service['name']}</b>\n\n"
        f"<b>Крок 2/5:</b> Введіть назву закладу:",
        reply_markup=cancel_kb
    )
    await state.set_state(AddPlaceStates.waiting_for_name)
    await callback.answer()


@router.message(AddPlaceStates.waiting_for_name)
async def process_place_name(message: Message, state: FSMContext):
    """Обробка введення назви."""
    if not is_admin(message.from_user.id):
        return
    
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("❌ Назва занадто коротка. Введіть знову:")
        return
    
    await state.update_data(name=name)
    data = await state.get_data()
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="addplace_cancel")]
    ])
    
    await message.answer(
        f"🏢 <b>Додавання закладу</b>\n\n"
        f"✅ Категорія: <b>{data['service_name']}</b>\n"
        f"✅ Назва: <b>{name}</b>\n\n"
        f"<b>Крок 3/5:</b> Введіть опис закладу:",
        reply_markup=cancel_kb
    )
    await state.set_state(AddPlaceStates.waiting_for_description)


@router.message(AddPlaceStates.waiting_for_description)
async def process_place_description(message: Message, state: FSMContext):
    """Обробка введення опису."""
    if not is_admin(message.from_user.id):
        return
    
    description = message.text.strip()
    await state.update_data(description=description)
    data = await state.get_data()
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="addplace_cancel")]
    ])
    
    await message.answer(
        f"🏢 <b>Додавання закладу</b>\n\n"
        f"✅ Категорія: <b>{data['service_name']}</b>\n"
        f"✅ Назва: <b>{data['name']}</b>\n"
        f"✅ Опис: {description[:50]}{'...' if len(description) > 50 else ''}\n\n"
        f"<b>Крок 4/5:</b> Введіть адресу:\n"
        f"<i>Формат: Брістоль (24-б), зі сторони Бермінгема</i>",
        reply_markup=cancel_kb
    )
    await state.set_state(AddPlaceStates.waiting_for_address)


@router.message(AddPlaceStates.waiting_for_address)
async def process_place_address(message: Message, state: FSMContext):
    """Обробка введення адреси."""
    if not is_admin(message.from_user.id):
        return
    
    address = message.text.strip()
    await state.update_data(address=address)
    data = await state.get_data()
    
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустити", callback_data="addplace_skip_keywords")],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="addplace_cancel")]
    ])
    
    await message.answer(
        f"🏢 <b>Додавання закладу</b>\n\n"
        f"✅ Категорія: <b>{data['service_name']}</b>\n"
        f"✅ Назва: <b>{data['name']}</b>\n"
        f"✅ Опис: {data['description'][:50]}{'...' if len(data['description']) > 50 else ''}\n"
        f"✅ Адреса: {address}\n\n"
        f"<b>Крок 5/5:</b> Введіть ключові слова (через кому):\n"
        f"<i>Приклад: кава,сирники,сніданок,wifi</i>\n\n"
        f"Або натисніть \"Пропустити\" щоб залишити порожнім.",
        reply_markup=skip_kb
    )
    await state.set_state(AddPlaceStates.waiting_for_keywords)


@router.message(AddPlaceStates.waiting_for_keywords)
async def process_place_keywords(message: Message, state: FSMContext):
    """Обробка введення ключових слів."""
    if not is_admin(message.from_user.id):
        return
    
    keywords = message.text.strip()
    await state.update_data(keywords=keywords)
    
    # Зберігаємо заклад
    await save_new_place(message, state)


@router.callback_query(F.data == "addplace_skip_keywords")
async def skip_keywords(callback: CallbackQuery, state: FSMContext):
    """Пропустити ключові слова."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Тільки для адміністраторів", show_alert=True)
        return
    
    await state.update_data(keywords=None)
    await callback.answer()
    
    # Зберігаємо заклад
    await save_new_place(callback.message, state, edit_message=True)


@router.callback_query(F.data == "addplace_cancel")
async def cancel_add_place(callback: CallbackQuery, state: FSMContext):
    """Скасувати додавання закладу."""
    await state.clear()
    await callback.message.edit_text("❌ Додавання закладу скасовано.")
    await callback.answer()


async def save_new_place(message: Message, state: FSMContext, edit_message: bool = False):
    """Зберегти новий заклад в БД."""
    from database import add_place, get_general_service
    
    data = await state.get_data()
    
    place_id = await add_place(
        service_id=data["service_id"],
        name=data["name"],
        description=data["description"],
        address=data["address"],
        keywords=data.get("keywords")
    )
    
    keywords_text = f"\n🏷 Ключові слова: {data['keywords']}" if data.get("keywords") else ""
    
    result_text = (
        f"✅ <b>Заклад успішно додано!</b>\n\n"
        f"📋 ID: <code>{place_id}</code>\n"
        f"📁 Категорія: {data['service_name']}\n"
        f"🏢 Назва: <b>{data['name']}</b>\n"
        f"📝 Опис: {data['description'][:100]}{'...' if len(data['description']) > 100 else ''}\n"
        f"📍 Адреса: {data['address']}{keywords_text}"
    )
    
    if edit_message:
        await message.edit_text(result_text)
    else:
        await message.answer(result_text)
    
    await state.clear()


@router.message(Command("edit_place"))
async def cmd_edit_place(message: Message):
    """Редагувати заклад."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/edit_place ІД_закладу ІД_категорії;Назва;Опис;Адреса;Ключові слова</code>\n"
            "Приклад: <code>/edit_place 1 1;Нова назва;Новий опис;Нова адреса;кава,сирники</code>\n\n"
            "⚠️ Ключові слова — необов'язковий параметр"
        )
        return
    
    try:
        place_id = int(args[1])
    except ValueError:
        await message.answer("❌ ІД закладу має бути числом.")
        return
    
    parts = args[2].split(";")
    if len(parts) < 4:
        await message.answer(
            "❌ Потрібно мінімум 4 параметри розділені крапкою з комою (;):\n"
            "<code>ІД_категорії;Назва;Опис;Адреса</code>"
        )
        return
    
    try:
        service_id = int(parts[0].strip())
    except ValueError:
        await message.answer("❌ ІД категорії має бути числом.")
        return
    
    name = parts[1].strip()
    description = parts[2].strip()
    address = parts[3].strip()
    keywords = parts[4].strip() if len(parts) > 4 else None
    
    from database import edit_place
    
    if await edit_place(place_id, service_id, name, description, address, keywords):
        await message.answer(f"✅ Заклад ID={place_id} оновлено.")
    else:
        await message.answer(f"❌ Заклад з ID={place_id} не знайдено.")


@router.message(Command("set_keywords"))
async def cmd_set_keywords(message: Message):
    """Встановити ключові слова для закладу."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/set_keywords ІД_закладу ключові,слова,через,кому</code>\n"
            "Приклад: <code>/set_keywords 1 кава,сирники,сніданок,десерт</code>"
        )
        return
    
    try:
        place_id = int(args[1])
    except ValueError:
        await message.answer("❌ ІД закладу має бути числом.")
        return
    
    keywords = args[2].strip()
    
    from database import update_place_keywords, get_place
    
    place = await get_place(place_id)
    if not place:
        await message.answer(f"❌ Заклад з ID={place_id} не знайдено.")
        return
    
    await update_place_keywords(place_id, keywords)
    await message.answer(
        f"✅ Ключові слова для <b>{place['name']}</b> оновлено:\n🏷 {keywords}"
    )


@router.message(Command("delete_place"))
async def cmd_delete_place(message: Message):
    """Видалити заклад."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Невірний формат.\n\n"
            "Використання: <code>/delete_place ІД</code>\n"
            "Приклад: <code>/delete_place 1</code>"
        )
        return
    
    try:
        place_id = int(args[1])
    except ValueError:
        await message.answer("❌ ІД має бути числом.")
        return
    
    from database import delete_place
    if await delete_place(place_id):
        await message.answer(f"✅ Заклад ID={place_id} видалено.")
    else:
        await message.answer(f"❌ Заклад з ID={place_id} не знайдено.")


@router.message(Command("list_places"))
async def cmd_list_places(message: Message):
    """Показати всі заклади з пагінацією."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ця команда доступна тільки адміністраторам.")
        return
    
    from database import get_all_places
    
    places = await get_all_places()
    
    if not places:
        await message.answer("📋 Закладів немає.")
        return
    
    # Групуємо заклади по категоріям
    categories = {}
    for p in places:
        cat_name = p["service_name"]
        if cat_name not in categories:
            categories[cat_name] = []
        categories[cat_name].append(p)
    
    # Формуємо повідомлення з урахуванням ліміту Telegram (4096 символів)
    MAX_LENGTH = 3800  # Залишаємо запас
    messages = []
    current_msg = "📋 <b>Всі заклади:</b>\n"
    
    for cat_name, cat_places in categories.items():
        cat_header = f"\n<b>{cat_name}:</b>\n"
        cat_content = ""
        
        for p in cat_places:
            place_line = f"  • ID={p['id']}: {p['name']}\n"
            if p["address"]:
                place_line += f"    📍 {p['address']}\n"
            cat_content += place_line
        
        # Перевіряємо чи вміститься категорія в поточне повідомлення
        if len(current_msg) + len(cat_header) + len(cat_content) > MAX_LENGTH:
            # Зберігаємо поточне повідомлення і починаємо нове
            if current_msg.strip():
                messages.append(current_msg.strip())
            current_msg = f"📋 <b>Всі заклади (продовження):</b>\n{cat_header}{cat_content}"
        else:
            current_msg += cat_header + cat_content
    
    # Додаємо останнє повідомлення
    if current_msg.strip():
        messages.append(current_msg.strip())
    
    # Відправляємо всі повідомлення
    total = len(places)
    for i, msg in enumerate(messages, 1):
        if len(messages) > 1:
            msg += f"\n\n<i>📊 Всього: {total} закладів (частина {i}/{len(messages)})</i>"
        else:
            msg += f"\n\n<i>📊 Всього: {total} закладів</i>"
        await message.answer(msg)
        if i < len(messages):
            import asyncio
            await asyncio.sleep(0.3)  # Невелика затримка між повідомленнями


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
    from database import get_heating_stats, get_subscriber_building, get_building_by_id
    
    building_id = await get_subscriber_building(user_id)
    building = get_building_by_id(building_id) if building_id else None
    
    if not building:
        return (
            "🔥 <b>Стан опалення</b>\n\n"
            "⚠️ Ви ще не обрали свій будинок.\n"
            "Натисніть «🏠 Обрати будинок» щоб голосувати по вашому будинку."
        )
    
    building_name = f"{building['name']} ({building['address']})"
    stats = await get_heating_stats(building_id)
    
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
    from database import get_water_stats, get_subscriber_building, get_building_by_id
    
    building_id = await get_subscriber_building(user_id)
    building = get_building_by_id(building_id) if building_id else None
    
    if not building:
        return (
            "💧 <b>Стан води</b>\n\n"
            "⚠️ Ви ще не обрали свій будинок.\n"
            "Натисніть «🏠 Обрати будинок» щоб голосувати по вашому будинку."
        )
    
    building_name = f"{building['name']} ({building['address']})"
    stats = await get_water_stats(building_id)
    
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
    await callback.answer()


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
    await callback.answer()


# --- Голосування зі сповіщень (без оновлення повідомлення) ---

@router.callback_query(F.data == "vote_heating_yes")
async def cb_vote_heating_yes(callback: CallbackQuery):
    """Проголосувати: є опалення (зі сповіщення)."""
    from database import vote_heating
    await vote_heating(callback.message.chat.id, True)
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що опалення є.", show_alert=True)


@router.callback_query(F.data == "vote_heating_no")
async def cb_vote_heating_no(callback: CallbackQuery):
    """Проголосувати: немає опалення (зі сповіщення)."""
    from database import vote_heating
    await vote_heating(callback.message.chat.id, False)
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що опалення немає.", show_alert=True)


@router.callback_query(F.data == "vote_water_yes")
async def cb_vote_water_yes(callback: CallbackQuery):
    """Проголосувати: є вода (зі сповіщення)."""
    from database import vote_water
    await vote_water(callback.message.chat.id, True)
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що вода є.", show_alert=True)


@router.callback_query(F.data == "vote_water_no")
async def cb_vote_water_no(callback: CallbackQuery):
    """Проголосувати: немає води (зі сповіщення)."""
    from database import vote_water
    await vote_water(callback.message.chat.id, False)
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що води немає.", show_alert=True)


# --- Голосування з меню (з оновленням статусу) ---

@router.callback_query(F.data == "menu_vote_heating_yes")
async def cb_menu_vote_heating_yes(callback: CallbackQuery):
    """Проголосувати: є опалення (з меню, оновлює статус)."""
    from database import vote_heating, get_user_vote
    await vote_heating(callback.message.chat.id, True)
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що опалення є.", show_alert=True)
    
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
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що опалення немає.", show_alert=True)
    
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
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що вода є.", show_alert=True)
    
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
    await callback.answer("✅ Дякуємо за голос! Ви повідомили, що води немає.", show_alert=True)
    
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
    await message.answer(
        "🔍 <b>Пошук закладів</b>\n\n"
        "Введіть назву, опис або ключове слово для пошуку.\n"
        "Наприклад: <i>сирники</i>, <i>кава</i>, <i>аптека</i>\n\n"
        "💡 Також можете шукати в будь-якому чаті через inline-режим:\n"
        f"<code>@{CFG.bot_username} сирники</code>\n\n"
        "⚡ Якщо напишете слово <b>світло</b>, бот покаже поточний статус електрики.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="menu")],
        ])
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
    await callback.answer()


async def do_search(query: str, user_id: int | None = None) -> str:
    """Виконати пошук та повернути форматований результат."""
    from database import search_places

    # Якщо запит містить 'світло' — показуємо статус світла і не шукаємо заклади
    if is_light_query(query):
        if user_id:
            text = await format_light_status(user_id, include_vote_prompt=False)
            return text
        else:
            # Fallback для inline режиму без user_id
            last = await db_get("last_state")
            if last is None:
                return "Ще немає даних. Зачекай 1-2 цикли перевірки."
            is_up = last == "up"
            last_event = await get_last_event()
            last_change = last_event[1] if last_event else None
            from weather import get_weather_line
            weather_text = await get_weather_line()
            return f"{state_text(is_up, last_change=last_change)}{weather_text}"
    
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
    
    # Якщо запит про світло — повертаємо один результат зі статусом світла
    if is_light_query(query):
        last = await db_get("last_state")
        if last is None:
            text = "Ще немає даних. Зачекай 1-2 цикли перевірки."
        else:
            is_up = last == "up"
            last_event = await get_last_event()
            last_change = last_event[1] if last_event else None
            from weather import get_weather_line
            weather_text = await get_weather_line()
            text = f"{state_text(is_up, last_change=last_change)}{weather_text}"
        articles = [
            InlineQueryResultArticle(
                id="light_status",
                title="Статус світла",
                description="Поточний стан електропостачання",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML"
                )
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
    
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Новий пошук", callback_data="search_menu")],
            [InlineKeyboardButton(text="« Меню", callback_data="menu")],
        ])
    )
