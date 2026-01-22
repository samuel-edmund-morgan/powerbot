import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from icmplib import ping

from config import CFG
from database import (
    db_get, db_set, add_event, get_last_event, get_subscribers_for_notification, 
    get_events_since, reset_votes, save_notification, get_active_notifications, 
    delete_notification, clear_all_notifications, get_heating_stats, get_water_stats,
    get_subscribers_for_light_notification, get_subscribers_for_alert_notification,
    NEWCASTLE_BUILDING_ID
)


def ping_ip(ip: str) -> bool:
    """
    Один пінг до конкретної IP адреси.
    Працює без root після налаштування net.ipv4.ping_group_range.
    """
    try:
        r = ping(ip, count=1, timeout=CFG.timeout_sec, privileged=False)
        return r.is_alive
    except Exception:
        return False


async def ping_all_ips() -> tuple[int, int]:
    """
    Пінгує всі IP адреси паралельно.
    Повертає (кількість успішних, загальна кількість).
    """
    tasks = [asyncio.to_thread(ping_ip, ip) for ip in CFG.home_ips]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = sum(1 for r in results if r is True)
    total = len(CFG.home_ips)
    
    return successful, total


async def evaluate_state() -> bool:
    """
    Оцінка поточного стану на основі кількох IP.
    
    Логіка:
    - Пінгуємо всі IP адреси
    - Якщо відсоток недоступних >= DOWN_THRESHOLD → стан DOWN
    - Інакше стан UP
    
    Для захисту від хибних спрацювань перевіряємо кілька разів.
    """
    # Швидка перевірка — якщо не досягли мінімальної кількості недоступних IP або
    # частка нижча за поріг, одразу UP.
    for _ in range(CFG.successes_to_up):
        successful, total = await ping_all_ips()
        if total == 0:
            return False

        failed = total - successful
        min_required = min(CFG.min_fail_hosts, total)
        fail_ratio = failed / total

        if failed < min_required or fail_ratio < CFG.down_threshold:
            return True
        await asyncio.sleep(0.15)

    # Детальна перевірка — кілька спроб підряд
    fail_count = 0
    for _ in range(CFG.fails_to_down):
        successful, total = await ping_all_ips()
        if total == 0:
            fail_count += 1
            continue

        failed = total - successful
        min_required = min(CFG.min_fail_hosts, total)
        fail_ratio = failed / total

        if failed >= min_required and fail_ratio >= CFG.down_threshold:
            fail_count += 1
        else:
            return True
        await asyncio.sleep(0.15)

    return False


def state_text(is_up: bool, short: bool = False, last_change: datetime | None = None) -> str:
    """
    Текстове представлення стану.
    
    Args:
        is_up: True якщо світло є, False якщо немає
        short: True для короткого формату (без пояснень)
        last_change: час останньої зміни стану (опціонально)
    """
    # Форматування часу останньої зміни
    time_text = ""
    if last_change:
        # Форматуємо час у зручному форматі
        time_str = last_change.strftime("%d.%m.%Y о %H:%M")
        if is_up:
            time_text = f"\n🕐 Увімкнули: {time_str}"
        else:
            time_text = f"\n🕐 Вимкнули: {time_str}"
    
    if short:
        return ("✅ Є світло!" if is_up else "❌ Немає світла") + time_text
    
    phone = CFG.electrician_phone
    phone_text = f"📞 Черговий електрик: <code>{phone}</code>" if phone else ""
    
    if is_up:
        advice = (
            "💡 Якщо у вашій квартирі відсутнє світло — "
            "ймовірно, вибило автомат у вашій квартирі або секції."
        )
        if phone_text:
            advice += f"\n{phone_text}"
        return f"✅ Є світло!{time_text}\n\n{advice}"
    else:
        advice = (
            "💡 Якщо у вас світло досі є — "
            "це означає, що відсутня електроенергія в одній із секцій будинку."
        )
        if phone_text:
            advice += f"\n{phone_text}"
        return f"❌ Немає світла{time_text}\n\n{advice}"


def format_duration(seconds: float) -> str:
    """Форматувати тривалість у зручний для читання формат."""
    total_seconds = int(seconds)
    
    if total_seconds < 60:
        return f"{total_seconds} сек"
    
    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24
    
    if days > 0:
        hours = hours % 24
        minutes = minutes % 60
        return f"{days}д {hours}г {minutes}хв"
    elif hours > 0:
        minutes = minutes % 60
        return f"{hours}г {minutes}хв"
    else:
        return f"{minutes}хв"


async def calculate_stats(period_days: int | None = None) -> dict:
    """
    Обчислити статистику відключень.
    period_days: кількість днів для аналізу (None = весь час)
    
    Повертає словник:
    {
        'total_downtime': float (секунди),
        'total_uptime': float (секунди),
        'uptime_percent': float,
        'outage_count': int,
        'period_start': datetime,
        'period_end': datetime,
    }
    """
    now = datetime.now()
    
    if period_days:
        since = now - timedelta(days=period_days)
        events = await get_events_since(since)
    else:
        from database import get_all_events
        events = await get_all_events()
        since = events[0][1] if events else now
    
    if not events:
        return {
            'total_downtime': 0,
            'total_uptime': 0,
            'uptime_percent': 100.0,
            'outage_count': 0,
            'period_start': since,
            'period_end': now,
        }
    
    total_downtime = 0.0
    total_uptime = 0.0
    outage_count = 0
    
    # Обробляємо події парами
    for i in range(len(events)):
        event_type, event_time = events[i]
        
        # Визначаємо кінець періоду
        if i + 1 < len(events):
            next_time = events[i + 1][1]
        else:
            next_time = now
        
        duration = (next_time - event_time).total_seconds()
        
        if event_type == "down":
            total_downtime += duration
            outage_count += 1
        else:
            total_uptime += duration
    
    total_time = total_uptime + total_downtime
    uptime_percent = (total_uptime / total_time * 100) if total_time > 0 else 100.0
    
    return {
        'total_downtime': total_downtime,
        'total_uptime': total_uptime,
        'uptime_percent': uptime_percent,
        'outage_count': outage_count,
        'period_start': since,
        'period_end': now,
    }


async def monitor_loop(bot: Bot):
    """
    Головний цикл моніторингу.
    Перевіряє стан роутера і надсилає сповіщення при зміні.
    """
    last = await db_get("last_state")
    last_state = None if last is None else (last == "up")

    while True:
        try:
            current = await evaluate_state()

            if last_state is None:
                last_state = current
                await db_set("last_state", "up" if current else "down")
                # Записуємо початковий стан в історію
                await add_event("up" if current else "down")

            if current != last_state:
                # Скидаємо голоси за опалення/воду при зміні стану світла (тільки для Ньюкасла)
                await reset_votes(NEWCASTLE_BUILDING_ID)
                
                # Обчислюємо тривалість попереднього стану
                duration_text = ""
                last_event = await get_last_event()
                if last_event:
                    _, last_timestamp = last_event
                    duration = (datetime.now() - last_timestamp).total_seconds()
                    if not current:
                        # Світло зникло — показуємо скільки було
                        duration_text = f"\n🕐 Було увімкнено: {format_duration(duration)}"
                    else:
                        # Світло з'явилось — показуємо скільки не було
                        duration_text = f"\n🕐 Не було: {format_duration(duration)}"

                # Записуємо подію в історію
                await add_event("up" if current else "down")
                
                last_state = current
                await db_set("last_state", "up" if current else "down")
                
                # Додаємо погоду до сповіщення
                from weather import get_weather_line
                weather_text = await get_weather_line()
                
                # Текст з проханням проголосувати
                vote_text = "\n\n👇 <b>Допоможи сусідам!</b> Повідом, чи є опалення та вода:"
                
                text = f"{state_text(current)}{duration_text}{weather_text}{vote_text}"
                
                # Перевіряємо глобальний прапорець сповіщень про світло
                global_enabled = (await db_get("light_notifications_global")) != "off"
                if not global_enabled:
                    # При вимкнених сповіщеннях очищуємо активні нотифікації й пропускаємо розсилку
                    await clear_all_notifications()
                    logging.info("Light notifications are globally disabled; skipping send")
                else:
                    # Створюємо клавіатуру для голосування
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    vote_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text="♨️ Є опалення", callback_data="vote_heating_yes"),
                            InlineKeyboardButton(text="❄️ Немає", callback_data="vote_heating_no"),
                        ],
                        [
                            InlineKeyboardButton(text="💧 Є вода", callback_data="vote_water_yes"),
                            InlineKeyboardButton(text="🚫 Немає", callback_data="vote_water_no"),
                        ],
                        [
                            InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu"),
                        ],
                    ])

                    # Очищаємо старі сповіщення перед надсиланням нових
                    await clear_all_notifications()

                    # Надсилаємо тільки підписникам будинку Ньюкасл з увімкненими сповіщеннями
                    # (поточна реалізація - тільки один сенсор на Ньюкаслі)
                    current_hour = datetime.now().hour
                    for chat_id in await get_subscribers_for_light_notification(current_hour, NEWCASTLE_BUILDING_ID):
                        try:
                            msg = await bot.send_message(chat_id, text, reply_markup=vote_keyboard)
                            # Зберігаємо message_id для подальшого оновлення
                            await save_notification(chat_id, msg.message_id)
                        except Exception:
                            logging.exception("Failed to notify chat_id=%s", chat_id)
                        await asyncio.sleep(0.04)  # 40ms затримка = 25 msg/sec (захист від rate limit)

        except Exception:
            logging.exception("monitor_loop error")

        await asyncio.sleep(CFG.check_interval)


async def update_notifications_loop(bot: Bot):
    """
    Фоновий цикл для оновлення сповіщень зі статистикою голосування.
    Оновлює кожні 30 секунд.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    while True:
        try:
            await asyncio.sleep(30)  # Оновлення кожні 30 секунд
            
            notifications = await get_active_notifications()
            if not notifications:
                continue
            
            # Отримуємо поточну статистику по будинку Ньюкасл
            heating_stats = await get_heating_stats(NEWCASTLE_BUILDING_ID)
            water_stats = await get_water_stats(NEWCASTLE_BUILDING_ID)
            
            # Формуємо блок опалення
            if heating_stats["total"] > 0:
                heating_text = (
                    f"\n\n♨️ <b>Опалення:</b> "
                    f"✅ {heating_stats['has_percent']}% | ❄️ {heating_stats['has_not_percent']}% "
                    f"({heating_stats['total']} голосів)"
                )
            else:
                heating_text = "\n\n♨️ <b>Опалення:</b> ще ніхто не голосував"
            
            # Формуємо блок води
            if water_stats["total"] > 0:
                water_text = (
                    f"\n💧 <b>Вода:</b> "
                    f"✅ {water_stats['has_percent']}% | 🚫 {water_stats['has_not_percent']}% "
                    f"({water_stats['total']} голосів)"
                )
            else:
                water_text = "\n💧 <b>Вода:</b> ще ніхто не голосував"
            
            # Отримуємо поточний стан
            current_state = await db_get("state")
            current = current_state == "up"
            
            # Отримуємо тривалість
            last_event = await get_last_event()
            duration_text = ""
            if last_event:
                event_type, last_ts = last_event
                delta = datetime.now() - last_ts
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes = remainder // 60
                if hours > 0:
                    duration_text = f"\n⏱ {hours} год {minutes} хв"
                else:
                    duration_text = f"\n⏱ {minutes} хв"
            
            # Отримуємо погоду
            weather_text = ""
            try:
                from weather import get_current_weather
                temp, desc = await get_current_weather()
                if temp is not None:
                    weather_text = f"\n🌡 Погода: {temp}°C, {desc}"
            except Exception:
                pass
            
            # Формуємо повний текст
            text = f"{state_text(current)}{duration_text}{weather_text}{heating_text}{water_text}"
            
            # Клавіатура для голосування
            vote_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="♨️ Є опалення", callback_data="vote_heating_yes"),
                    InlineKeyboardButton(text="❄️ Немає", callback_data="vote_heating_no"),
                ],
                [
                    InlineKeyboardButton(text="💧 Є вода", callback_data="vote_water_yes"),
                    InlineKeyboardButton(text="🚫 Немає", callback_data="vote_water_no"),
                ],
                [
                    InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu"),
                ],
            ])
            
            # Оновлюємо всі сповіщення
            for notif in notifications:
                try:
                    await bot.edit_message_text(
                        text=text,
                        chat_id=notif["chat_id"],
                        message_id=notif["message_id"],
                        reply_markup=vote_keyboard
                    )
                except Exception as e:
                    # Якщо не вдалось оновити (наприклад, повідомлення видалено)
                    if "message is not modified" not in str(e).lower():
                        logging.debug("Failed to update notification %s: %s", notif["id"], e)
                        await delete_notification(notif["id"])
                
                # Невелика затримка між оновленнями для уникнення rate limit
                await asyncio.sleep(0.05)
        
        except Exception:
            logging.exception("update_notifications_loop error")


async def alert_monitor_loop(bot: Bot):
    """
    Цикл моніторингу повітряних тривог для м. Київ.
    Перевіряє стан тривоги і надсилає сповіщення при зміні.
    """
    from alerts import check_alert_status, alert_text
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Отримуємо збережений стан тривоги
    last = await db_get("last_alert_state")
    last_alert_state = None if last is None else (last == "active")
    
    # Інтервал перевірки тривог (секунди)
    # Ukrainealarm API має rate limit, тому ставимо 60 секунд
    ALERT_CHECK_INTERVAL = 60
    
    while True:
        try:
            current = await check_alert_status()
            
            # Якщо помилка запиту - пропускаємо цикл
            if current is None:
                await asyncio.sleep(ALERT_CHECK_INTERVAL)
                continue
            
            # Перший запуск - просто зберігаємо стан
            if last_alert_state is None:
                last_alert_state = current
                await db_set("last_alert_state", "active" if current else "inactive")
                logging.info(f"Initial alert state: {'ACTIVE' if current else 'inactive'}")
            
            # Зміна стану тривоги
            if current != last_alert_state:
                last_alert_state = current
                await db_set("last_alert_state", "active" if current else "inactive")
                
                logging.info(f"Alert state changed: {'ACTIVE' if current else 'inactive'}")
                
                text = alert_text(current)
                
                # Клавіатура з кнопкою меню
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
                ])
                
                # Надсилаємо тільки тим, хто увімкнув сповіщення про тривоги
                current_hour = datetime.now().hour
                for chat_id in await get_subscribers_for_alert_notification(current_hour):
                    try:
                        await bot.send_message(chat_id, text, reply_markup=keyboard)
                    except Exception:
                        logging.exception("Failed to send alert to chat_id=%s", chat_id)
                    await asyncio.sleep(0.04)  # 40ms затримка = 25 msg/sec (захист від rate limit)
        
        except Exception:
            logging.exception("alert_monitor_loop error")
        
        await asyncio.sleep(ALERT_CHECK_INTERVAL)
