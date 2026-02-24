import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

from config import CFG
from database import (
    db_get, db_set, add_event, get_last_event, get_subscribers_for_notification, 
    get_events_since, reset_votes, save_notification, get_active_notifications, 
    delete_notification, get_heating_stats, get_water_stats,
    get_last_bot_message, delete_last_bot_message_record,
    get_subscribers_for_light_notification, get_subscribers_for_alert_notification,
    NEWCASTLE_BUILDING_ID, get_all_active_sensors,
    get_sensors_by_building, get_building_by_id,
    get_last_events, remove_subscriber,
    get_last_event_before,
    get_subscriber_building_and_section,
    default_section_for_building,
    VALID_SECTION_IDS,
    get_all_building_sections_power_state,
    get_building_section_power_state,
    set_building_section_power_state,
)

# Налаштування масових розсилок (можна перевизначити через env)
BROADCAST_RATE_PER_SEC = float(os.getenv("BROADCAST_RATE_PER_SEC", "20"))
BROADCAST_CONCURRENCY = int(os.getenv("BROADCAST_CONCURRENCY", "8"))
BROADCAST_MAX_RETRIES = int(os.getenv("BROADCAST_MAX_RETRIES", "1"))

# Синхронізація записів у БД для уникнення SQLite lock при масовій розсилці
_notification_save_lock = asyncio.Lock()


def _get_unique_sensor_alias_source_for_target(
    building_id: int,
    section_id: int,
) -> tuple[int, int] | None:
    """If (building_id, section_id) is an alias target, return a unique source section.

    We only return a source when there is exactly 1 distinct source mapping to this target.
    If multiple sources map to the same target (OR-state), history/statistics become ambiguous.
    """
    aliases = getattr(CFG, "sensor_aliases", None) or {}
    if not aliases:
        return None

    sources: list[tuple[int, int]] = []
    for (src_bid, src_sid), dsts in aliases.items():
        for (dst_bid, dst_sid) in dsts:
            if int(dst_bid) == int(building_id) and int(dst_sid) == int(section_id):
                sources.append((int(src_bid), int(src_sid)))

    uniq = list(dict.fromkeys(sources))  # de-dup preserve order
    if len(uniq) == 1:
        return uniq[0]
    return None


async def format_light_status(
    user_id: int,
    include_vote_prompt: bool = False,
    heating_stats: dict | None = None,
    water_stats: dict | None = None,
    override_building_id: int | None = None,
    override_section_id: int | None = None,
) -> str:
    """
    Форматувати статус світла зі шкалою для будинку користувача.
    Використовується як у хендлері, так і в сповіщеннях.
    """
    from weather import get_weather_line

    perf_start = asyncio.get_running_loop().time()
    user_building_id, user_section_id = await get_subscriber_building_and_section(user_id)
    if override_building_id is not None:
        try:
            user_building_id = int(override_building_id)
        except Exception:
            user_building_id = None
    if override_section_id is not None:
        try:
            user_section_id = int(override_section_id)
        except Exception:
            user_section_id = None
    elif override_building_id is not None:
        user_section_id = default_section_for_building(user_building_id)
    perf_after_building = asyncio.get_running_loop().time()
    user_building = get_building_by_id(user_building_id) if user_building_id else None

    sensors = await get_sensors_by_building(user_building_id) if user_building_id else []
    perf_after_sensors = asyncio.get_running_loop().time()
    building_sensors_total = len(sensors)

    building_sensors_online = 0
    section_sensors_total = 0
    section_sensors_online = 0
    # Physical (non-aliased) section states for this building.
    physical_section_any_online: dict[int, bool] = {}
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    for s in sensors:
        sensor_section = s.get("section_id")
        if sensor_section is None:
            sensor_section = default_section_for_building(user_building_id)

        frozen_until = s.get("frozen_until")
        frozen_active = bool(frozen_until and frozen_until > now)
        if frozen_active:
            effective_online = (
                bool(s.get("frozen_is_up")) if s.get("frozen_is_up") is not None else False
            )
        else:
            effective_online = bool(s["last_heartbeat"] and (now - s["last_heartbeat"]) < timeout)

        physical_section_any_online[int(sensor_section)] = (
            physical_section_any_online.get(int(sensor_section), False) or effective_online
        )

        if effective_online:
            building_sensors_online += 1
            if user_section_id is not None and sensor_section == user_section_id:
                section_sensors_online += 1
        if user_section_id is not None and sensor_section == user_section_id:
            section_sensors_total += 1

    # Sensor aliases: treat source section state as a "virtual sensor" in target sections.
    # This is a temporary bridge for cases when one physical sensor represents multiple
    # sections/buildings (shared power line).
    alias_edges: list[tuple[int, int, int]] = []
    aliases = getattr(CFG, "sensor_aliases", None) or {}
    if user_building_id and aliases:
        for (src_bid, src_sid), dsts in aliases.items():
            for (dst_bid, dst_sid) in dsts:
                if int(dst_bid) == int(user_building_id):
                    alias_edges.append((int(src_bid), int(src_sid), int(dst_sid)))

    src_section_is_up: dict[tuple[int, int], bool] = {}
    if alias_edges:
        # Seed with states for this building (already computed from physical sensors).
        for sid, is_up in physical_section_any_online.items():
            src_section_is_up[(int(user_building_id), int(sid))] = bool(is_up)

        # Fetch minimal extra buildings needed to resolve cross-building aliases.
        src_building_ids = {src_bid for (src_bid, _src_sid, _dst_sid) in alias_edges if src_bid != int(user_building_id)}
        for src_bid in sorted(src_building_ids):
            src_sensors = await get_sensors_by_building(src_bid)
            any_online: dict[int, bool] = {}
            for sensor in src_sensors:
                sid = sensor.get("section_id")
                if sid is None:
                    sid = default_section_for_building(src_bid)
                if sid is None:
                    continue
                sid_int = int(sid)

                frozen_until = sensor.get("frozen_until")
                frozen_active = bool(frozen_until and frozen_until > now)
                if frozen_active:
                    effective_online = (
                        bool(sensor.get("frozen_is_up")) if sensor.get("frozen_is_up") is not None else False
                    )
                else:
                    effective_online = bool(sensor["last_heartbeat"] and (now - sensor["last_heartbeat"]) < timeout)

                any_online[sid_int] = any_online.get(sid_int, False) or effective_online

            for sid_int, is_up in any_online.items():
                src_section_is_up[(src_bid, sid_int)] = bool(is_up)

        # Apply virtual sensors to totals (one per alias edge).
        for (src_bid, src_sid, dst_sid) in alias_edges:
            src_is_up = bool(src_section_is_up.get((src_bid, src_sid), False))
            building_sensors_total += 1
            if src_is_up:
                building_sensors_online += 1
            if user_section_id is not None and int(dst_sid) == int(user_section_id):
                section_sensors_total += 1
                if src_is_up:
                    section_sensors_online += 1

    building_is_up = building_sensors_online > 0
    section_is_up: bool | None
    if user_section_id is None:
        section_is_up = None
    elif section_sensors_total == 0:
        section_is_up = None
    else:
        section_is_up = section_sensors_online > 0

    # Resolve history source for alias targets:
    # If user section is an alias target AND has no events of its own yet, we show history/stats
    # from the unique alias source (so UI isn't "0 сек | 0 сек").
    history_building_id = user_building_id
    history_section_id = user_section_id

    last_event = (
        await get_last_event(building_id=user_building_id, section_id=user_section_id)
        if user_building_id and user_section_id
        else None
    )
    if (
        last_event is None
        and user_building_id is not None
        and user_section_id in VALID_SECTION_IDS
    ):
        alias_src = _get_unique_sensor_alias_source_for_target(user_building_id, int(user_section_id))
        if alias_src:
            src_bid, src_sid = alias_src
            src_last = await get_last_event(building_id=src_bid, section_id=src_sid)
            if src_last is not None:
                history_building_id, history_section_id = src_bid, src_sid
                last_event = src_last

    perf_after_last_event = asyncio.get_running_loop().time()
    last_change_text = ""
    if last_event:
        event_type, event_time = last_event
        time_str = event_time.strftime("%d.%m.%Y о %H:%M")
        if event_type == "up":
            last_change_text = f"🕐 Увімкнули: {time_str}"
        else:
            last_change_text = f"🕐 Вимкнули: {time_str}"

    duration_text = ""
    last_events = (
        await get_last_events(2, building_id=history_building_id, section_id=history_section_id)
        if history_building_id and history_section_id
        else []
    )
    perf_after_last_events = asyncio.get_running_loop().time()
    if len(last_events) >= 2:
        last_type, last_time = last_events[0]
        prev_type, prev_time = last_events[1]
        duration_seconds = (last_time - prev_time).total_seconds()
        duration_formatted = format_duration(duration_seconds)
        if last_type == "down":
            duration_text = f"⏱ Було зі світлом: {duration_formatted}"
        else:
            duration_text = f"⏱ Було без світла: {duration_formatted}"

    stats = await calculate_stats(
        period_days=1,
        building_id=history_building_id,
        section_id=history_section_id,
    )
    perf_after_stats = asyncio.get_running_loop().time()
    today_uptime = format_duration(stats["total_uptime"])
    today_downtime = format_duration(stats["total_downtime"])
    stats_info = f"📊 Сьогодні: ✅ {today_uptime} | ❌ {today_downtime}"

    lines = ["☀️ <b>Стан електропостачання</b>\n"]

    if not user_building:
        lines.append("⚠️ Ви ще не обрали свій будинок.")
        lines.append("Натисніть «🏠 Обрати будинок» щоб отримувати точну інформацію.")
        return "\n".join(lines)
    if user_section_id not in VALID_SECTION_IDS:
        lines.append(f"🏠 <b>{user_building['name']} ({user_building['address']})</b>")
        lines.append("⚠️ Ви ще не обрали свою секцію.")
        lines.append("Натисніть «🏠 Обрати будинок» і оберіть секцію.")
        return "\n".join(lines)

    display_name = f"{user_building['name']} ({user_building['address']})"

    # Оновлюємо заголовок (після валідації building/section)
    lines[0] = (
        f"☀️ <b>Стан електропостачання в {user_building['name']} секція {user_section_id}</b>\n"
    )

    if building_sensors_total > 0:
        percent = round(building_sensors_online / building_sensors_total * 100)
        status_text = "✅ Світло є" if section_is_up else "❌ Світла немає"
        bar_length = 10
        filled = round(percent / 100 * bar_length)
        bar = "🟩" * filled + "🟥" * (bar_length - filled)
        lines.append(f"🏠 <b>{display_name}</b>")
        lines.append(f"{bar} <b>{percent}%</b>")
        if section_sensors_total > 0:
            lines.append(
                f"{status_text} (секція: {section_sensors_online}/{section_sensors_total}, "
                f"будинок: {building_sensors_online}/{building_sensors_total})"
            )
        else:
            lines.append(
                f"⚠️ Немає сенсора в цій секції "
                f"(будинок: {building_sensors_online}/{building_sensors_total})"
            )
    else:
        bar = "⬜" * 10
        lines.append(f"🏠 <b>{display_name}</b>")
        lines.append(f"{bar}")
        lines.append("⚠️ Сенсорів немає (в розробці)")

    if last_change_text:
        lines.append(f"\n{last_change_text}")
    if duration_text:
        lines.append(duration_text)
    lines.append(stats_info)

    if heating_stats is not None:
        if heating_stats["total"] > 0:
            lines.append(
                f"\n♨️ <b>Опалення:</b> ✅ {heating_stats['has_percent']}% | "
                f"❄️ {heating_stats['has_not_percent']}% ({heating_stats['total']} голосів)"
            )
        else:
            lines.append("\n♨️ <b>Опалення:</b> ще ніхто не голосував")

    if water_stats is not None:
        if water_stats["total"] > 0:
            lines.append(
                f"\n💧 <b>Вода:</b> ✅ {water_stats['has_percent']}% | "
                f"🚫 {water_stats['has_not_percent']}% ({water_stats['total']} голосів)"
            )
        else:
            lines.append("\n💧 <b>Вода:</b> ще ніхто не голосував")

    phone = CFG.electrician_phone
    if building_sensors_total > 0 and section_is_up is not None:
        if section_is_up:
            lines.append(
                "\n💡 Якщо у вашій квартирі відсутнє світло — "
                "ймовірно, вибило автомат у вашій квартирі або секції."
            )
        else:
            lines.append(
                "\n💡 Якщо у вас світло досі є — "
                "це означає, що відсутня електроенергія в одній із секцій будинку."
            )

    if phone:
        lines.append(f"📞 Черговий електрик: <code>{phone}</code>")

    weather_text = await get_weather_line()
    perf_after_weather = asyncio.get_running_loop().time()
    if weather_text:
        weather_line = weather_text.strip()
        if "Погода" in weather_line and not any("Погода" in line for line in lines):
            lines.append(weather_line)

    if CFG.yasno_enabled and user_building_id and user_section_id:
        try:
            from yasno import get_building_schedule_text

            schedule_text = await get_building_schedule_text(
                user_building_id,
                section_id=user_section_id,
                include_building=False,
            )
            if schedule_text and "не налаштовані" not in schedule_text and "не ввімкнені" not in schedule_text:
                lines.append("")
                lines.append(schedule_text)
        except Exception:
            logging.exception("Failed to get Yasno schedule")

    updated = datetime.now().strftime("%H:%M:%S")
    if not any("Оновлено" in line for line in lines):
        lines.append(f"Оновлено: {updated}")

    if include_vote_prompt:
        lines.append("\n👇 <b>Допоможи сусідам!</b> Повідом, чи є опалення та вода:")

    total_ms = (perf_after_weather - perf_start) * 1000
    if total_ms > 500:
        logging.info(
            "perf:format_light_status user=%s total=%.0fms building=%.0fms sensors=%.0fms last_event=%.0fms last_events=%.0fms stats=%.0fms weather=%.0fms",
            user_id,
            total_ms,
            (perf_after_building - perf_start) * 1000,
            (perf_after_sensors - perf_after_building) * 1000,
            (perf_after_last_event - perf_after_sensors) * 1000,
            (perf_after_last_events - perf_after_last_event) * 1000,
            (perf_after_stats - perf_after_last_events) * 1000,
            (perf_after_weather - perf_after_stats) * 1000,
        )

    return "\n".join(lines)


class BroadcastRateLimiter:
    """Глобальний rate limiter для масових розсилок (token-interval)."""

    def __init__(self, rate_per_sec: float):
        self._interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_time = 0.0

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            if now < self._next_time:
                await asyncio.sleep(self._next_time - now)
                now = loop.time()
            self._next_time = max(self._next_time, now) + self._interval


async def _broadcast_worker(
    queue: asyncio.Queue,
    limiter: BroadcastRateLimiter,
    send_fn,
    *,
    retries: int,
) -> None:
    while True:
        chat_id = await queue.get()
        if chat_id is None:
            queue.task_done()
            break
        try:
            attempt = 0
            while True:
                try:
                    await limiter.wait()
                    await send_fn(chat_id)
                    break
                except TelegramRetryAfter as exc:
                    attempt += 1
                    if attempt > retries:
                        raise
                    logging.warning(
                        "Telegram rate limit: retry_after=%s chat_id=%s attempt=%s",
                        exc.retry_after,
                        chat_id,
                        attempt,
                    )
                    await asyncio.sleep(exc.retry_after)
                except TelegramForbiddenError as exc:
                    # Користувач заблокував бота — прибираємо зі списку підписників
                    logging.warning(
                        "TelegramForbiddenError chat_id=%s; removing subscriber", chat_id
                    )
                    await remove_subscriber(chat_id)
                    break
                except TelegramBadRequest as exc:
                    msg = str(exc).lower()
                    # Типові кейси недійсних чатів/деактивованих акаунтів
                    if "chat not found" in msg or "user is deactivated" in msg:
                        logging.warning(
                            "TelegramBadRequest (%s) chat_id=%s; removing subscriber",
                            msg,
                            chat_id,
                        )
                        await remove_subscriber(chat_id)
                        break
                    raise
        except Exception:
            logging.exception("Failed to notify chat_id=%s", chat_id)
        finally:
            queue.task_done()


async def broadcast_messages(
    chat_ids: list[int],
    send_fn,
    *,
    rate_per_sec: float = BROADCAST_RATE_PER_SEC,
    concurrency: int = BROADCAST_CONCURRENCY,
    retries: int = BROADCAST_MAX_RETRIES,
) -> None:
    """Надіслати повідомлення паралельно з глобальним rate limit."""
    if not chat_ids:
        return
    limiter = BroadcastRateLimiter(rate_per_sec)
    queue: asyncio.Queue = asyncio.Queue()
    for chat_id in chat_ids:
        queue.put_nowait(chat_id)
    workers = [
        asyncio.create_task(
            _broadcast_worker(queue, limiter, send_fn, retries=retries)
        )
        for _ in range(max(1, concurrency))
    ]
    await queue.join()
    for _ in workers:
        queue.put_nowait(None)
    await asyncio.gather(*workers, return_exceptions=True)


# ============ Нова система моніторингу через ESP32 сенсори ============

async def check_sensors_timeout() -> dict[tuple[int, int], bool]:
    """
    Перевіряє таймаути всіх сенсорів.
    
    Повертає словник {(building_id, section_id): is_up}
    де is_up = True якщо хоча б один сенсор секції "живий"
    """
    sensors = await get_all_active_sensors()
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    
    # Групуємо сенсори по (будинок, секція)
    sections_sensors: dict[tuple[int, int], list[dict]] = {}
    for sensor in sensors:
        bid = sensor["building_id"]
        sid = sensor.get("section_id")
        if sid is None:
            sid = default_section_for_building(bid)
        if sid is None:
            continue
        key = (bid, int(sid))
        sections_sensors.setdefault(key, []).append(sensor)
    
    # Визначаємо стан кожної секції (base = physical sensors only)
    result: dict[tuple[int, int], bool] = {}
    for (building_id, section_id), section_sensors in sections_sensors.items():
        # Секція UP якщо хоча б один сенсор "живий"
        is_up = False
        for sensor in section_sensors:
            frozen_until = sensor.get("frozen_until")
            frozen_active = bool(frozen_until and frozen_until > now)
            if frozen_active:
                effective_online = (
                    bool(sensor.get("frozen_is_up")) if sensor.get("frozen_is_up") is not None else False
                )
            else:
                effective_online = bool(sensor["last_heartbeat"] and (now - sensor["last_heartbeat"]) < timeout)

            if effective_online:
                is_up = True
                break
        result[(building_id, section_id)] = is_up

    # Sensor aliases: treat "UP" from source section as additional "virtual sensor"
    # for target sections/buildings. We intentionally use *base* (physical) source
    # state to avoid recursive alias loops.
    aliases = getattr(CFG, "sensor_aliases", None) or {}
    if aliases:
        base_states = dict(result)
        for (src_bid, src_sid), targets in aliases.items():
            src_state = bool(base_states.get((src_bid, src_sid), False))
            for (dst_bid, dst_sid) in targets:
                dst_key = (int(dst_bid), int(dst_sid))
                result[dst_key] = bool(result.get(dst_key, False) or src_state)
    
    return result


async def get_building_sensors_status(building_id: int) -> dict:
    """
    Отримати детальний статус сенсорів будинку.
    
    Повертає:
    {
        "building_id": 1,
        "building_name": "Ньюкасл",
        "is_up": True/False,
        "sensors_total": 3,
        "sensors_online": 2,
        "sensors": [
            {"uuid": "...", "name": "...", "is_online": True, "last_seen": datetime}
        ]
    }
    """
    building = get_building_by_id(building_id)
    if not building:
        return None
    
    sensors = await get_sensors_by_building(building_id)
    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    
    sensors_status = []
    online_count = 0
    
    for sensor in sensors:
        is_online = False
        if sensor["last_heartbeat"]:
            time_since = now - sensor["last_heartbeat"]
            is_online = time_since < timeout
        
        if is_online:
            online_count += 1
        
        sensors_status.append({
            "uuid": sensor["uuid"],
            "name": sensor["name"],
            "is_online": is_online,
            "last_seen": sensor["last_heartbeat"],
        })
    
    return {
        "building_id": building_id,
        "building_name": building["name"],
        "is_up": online_count > 0,
        "sensors_total": len(sensors),
        "sensors_online": online_count,
        "sensors": sensors_status,
    }


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


async def calculate_stats(
    period_days: int | None = None,
    *,
    building_id: int | None = None,
    section_id: int | None = None,
) -> dict:
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
    # Alias-aware fallback:
    # If the requested section has no events at all, but it is an alias target with exactly
    # one source that has history, use the source history for stats.
    if building_id is not None and section_id is not None:
        try:
            last = await get_last_event(building_id=building_id, section_id=section_id)
        except Exception:
            last = None
        if last is None:
            alias_src = _get_unique_sensor_alias_source_for_target(int(building_id), int(section_id))
            if alias_src:
                src_bid, src_sid = alias_src
                try:
                    src_last = await get_last_event(building_id=src_bid, section_id=src_sid)
                except Exception:
                    src_last = None
                if src_last is not None:
                    building_id, section_id = src_bid, src_sid

    now = datetime.now()
    
    if period_days:
        if period_days == 1:
            since = datetime(now.year, now.month, now.day)
        else:
            since = now - timedelta(days=period_days)
        events = await get_events_since(since, building_id=building_id, section_id=section_id)
    else:
        from database import get_all_events
        events = await get_all_events(building_id=building_id, section_id=section_id)
        since = events[0][1] if events else now

    total_downtime = 0.0
    total_uptime = 0.0
    outage_count = 0

    # Визначаємо стан на початку періоду
    start_state = None  # "up" або "down"
    prev = await get_last_event_before(since, building_id=building_id, section_id=section_id)
    if prev:
        start_state = prev[0]
    elif events:
        start_state = "up" if events[0][0] == "down" else "down"
    else:
        last = await get_last_event(building_id=building_id, section_id=section_id)
        if last:
            start_state = last[0]

    cursor_time = since

    if events:
        first_type, first_time = events[0]
        if start_state in ("up", "down"):
            duration = (first_time - since).total_seconds()
            if duration > 0:
                if start_state == "down":
                    total_downtime += duration
                else:
                    total_uptime += duration
        cursor_time = first_time

        for event_type, event_time in events:
            duration = (event_time - cursor_time).total_seconds()
            if duration > 0 and start_state in ("up", "down"):
                if start_state == "down":
                    total_downtime += duration
                else:
                    total_uptime += duration

            start_state = event_type
            if event_type == "down":
                outage_count += 1
            cursor_time = event_time

        tail_duration = (now - cursor_time).total_seconds()
        if tail_duration > 0 and start_state in ("up", "down"):
            if start_state == "down":
                total_downtime += tail_duration
            else:
                total_uptime += tail_duration
    else:
        if start_state in ("up", "down"):
            duration = (now - since).total_seconds()
            if duration > 0:
                if start_state == "down":
                    total_downtime += duration
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


async def update_notifications_loop(bot: Bot):
    """
    Фоновий цикл для оновлення сповіщень зі статистикою голосування.
    Оновлює кожні 30 секунд.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    while True:
        try:
            await asyncio.sleep(30)  # Оновлення кожні 30 секунд

            notifications = await get_active_notifications("power_change")
            if not notifications:
                continue

            # Поточна статистика голосів по секціях (групуємо, щоб не робити N однакових запитів)
            grouped: dict[tuple[int, int], list[dict]] = {}
            for notif in notifications:
                building_id, section_id = await get_subscriber_building_and_section(notif["chat_id"])
                if building_id is None or section_id not in VALID_SECTION_IDS:
                    continue
                grouped.setdefault((building_id, section_id), []).append(notif)

            stats_cache: dict[tuple[int, int], tuple[dict, dict]] = {}
            for (building_id, section_id) in grouped:
                heating_stats = await get_heating_stats(building_id, section_id)
                water_stats = await get_water_stats(building_id, section_id)
                stats_cache[(building_id, section_id)] = (heating_stats, water_stats)
            
            # Клавіатура для голосування
            vote_rows = [
                [
                    InlineKeyboardButton(text="♨️ Є опалення", callback_data="vote_heating_yes"),
                    InlineKeyboardButton(text="❄️ Немає", callback_data="vote_heating_no"),
                ],
                [
                    InlineKeyboardButton(text="💧 Є вода", callback_data="vote_water_yes"),
                    InlineKeyboardButton(text="🚫 Немає", callback_data="vote_water_no"),
                ],
            ]
            vote_rows.append([InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")])
            vote_keyboard = InlineKeyboardMarkup(inline_keyboard=vote_rows)
            
            # Оновлюємо всі сповіщення
            for (building_id, section_id), notifs in grouped.items():
                heating_stats, water_stats = stats_cache[(building_id, section_id)]
                for notif in notifs:
                    try:
                        text = await format_light_status(
                            notif["chat_id"],
                            include_vote_prompt=False,
                            heating_stats=heating_stats,
                            water_stats=water_stats,
                        )
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
    # alerts.in.ua: soft limit 8-10 req/min, hard limit 12 req/min
    # 2 боти на одній IP = max 6 req/min на бота
    # За ALERTS_IN_UA_RATIO=7: ukrainealarm ~раз на 88 сек, решта alerts.in.ua (~5.4 req/min)
    ALERT_CHECK_INTERVAL = 11
    
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
                subscribers = await get_subscribers_for_alert_notification(current_hour)

                existing_alerts = {
                    notif["chat_id"]: notif
                    for notif in await get_active_notifications("alert")
                }

                async def send_alert(chat_id: int):
                    last_menu_id = await get_last_bot_message(chat_id)
                    if last_menu_id:
                        try:
                            await bot.delete_message(chat_id, last_menu_id)
                        except Exception:
                            pass
                        await delete_last_bot_message_record(chat_id)
                    prev = existing_alerts.get(chat_id)
                    if prev:
                        try:
                            await bot.delete_message(chat_id, prev["message_id"])
                        except Exception:
                            pass
                        await delete_notification(prev["id"])
                    msg = await bot.send_message(chat_id, text, reply_markup=keyboard)
                    await save_notification(chat_id, msg.message_id, "alert")

                await broadcast_messages(subscribers, send_alert)
        
        except Exception:
            logging.exception("alert_monitor_loop error")
        
        await asyncio.sleep(ALERT_CHECK_INTERVAL)


async def sensors_monitor_loop(bot: Bot):
    """
    Цикл моніторингу ESP32 сенсорів.
    Перевіряє таймаути heartbeat і надсилає сповіщення при зміні стану будинку.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Зберігаємо попередні стани секцій
    previous_states: dict[tuple[int, int], bool] = {}
    
    # Ініціалізуємо стани з БД
    initial_states = await get_all_building_sections_power_state()
    for (building_id, section_id), state in initial_states.items():
        previous_states[(building_id, section_id)] = state["is_up"]
    
    # Інтервал перевірки (секунди)
    CHECK_INTERVAL = 10
    
    while True:
        try:
            # Перевіряємо таймаути всіх сенсорів
            current_states = await check_sensors_timeout()
            
            for (building_id, section_id), is_up in current_states.items():
                # Отримуємо попередній стан
                prev_is_up = previous_states.get((building_id, section_id))
                
                # Якщо стан не змінився - пропускаємо
                if prev_is_up == is_up:
                    continue

                # Перший раз бачимо цю секцію після міграції/рестарту — ініціалізуємо без розсилки
                if prev_is_up is None:
                    await set_building_section_power_state(building_id, section_id, is_up)
                    previous_states[(building_id, section_id)] = is_up
                    logging.info(
                        "Initial section power state: building=%s section=%s state=%s",
                        building_id,
                        section_id,
                        "UP" if is_up else "DOWN",
                    )
                    continue
                
                # Оновлюємо стан в БД
                state_changed = await set_building_section_power_state(building_id, section_id, is_up)
                if not state_changed:
                    continue
                
                # Оновлюємо локальний кеш
                previous_states[(building_id, section_id)] = is_up
                
                # Отримуємо інформацію про будинок
                building = get_building_by_id(building_id)
                if not building:
                    continue
                
                # Скидаємо голоси за опалення/воду при зміні стану світла
                await reset_votes(building_id, section_id)
                
                # Записуємо подію в історію
                event_type = "up" if is_up else "down"
                await add_event(event_type, building_id=building_id, section_id=section_id)

                building_name = building["name"] if building else f"ID:{building_id}"
                logging.info(
                    "Building %s section %s power state changed to: %s",
                    building_name,
                    section_id,
                    "UP" if is_up else "DOWN",
                )
                
                # Перевіряємо глобальний прапорець сповіщень
                global_enabled = (await db_get("light_notifications_global")) != "off"
                if not global_enabled:
                    logging.info("Light notifications are globally disabled; skipping send")
                    continue
                
                # Клавіатура для голосування
                vote_rows = [
                    [
                        InlineKeyboardButton(text="♨️ Є опалення", callback_data="vote_heating_yes"),
                        InlineKeyboardButton(text="❄️ Немає", callback_data="vote_heating_no"),
                    ],
                    [
                        InlineKeyboardButton(text="💧 Є вода", callback_data="vote_water_yes"),
                        InlineKeyboardButton(text="🚫 Немає", callback_data="vote_water_no"),
                    ],
                ]
                vote_rows.append([InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")])
                vote_keyboard = InlineKeyboardMarkup(inline_keyboard=vote_rows)
                
                # Надсилаємо підписникам цього будинку
                current_hour = datetime.now().hour
                subscribers = await get_subscribers_for_light_notification(
                    current_hour,
                    building_id,
                    section_id,
                )
                existing_notifications = {
                    notif["chat_id"]: notif
                    for notif in await get_active_notifications("power_change")
                }

                async def send_light(chat_id: int):
                    # Формуємо уніфікований текст (як у хендлері) для конкретного користувача
                    # Голосування лишаємо клавіатурою, без текстового промпту.
                    text = await format_light_status(chat_id, include_vote_prompt=False)
                    last_menu_id = await get_last_bot_message(chat_id)
                    if last_menu_id:
                        try:
                            await bot.delete_message(chat_id, last_menu_id)
                        except Exception:
                            pass
                        await delete_last_bot_message_record(chat_id)
                    prev = existing_notifications.get(chat_id)
                    if prev:
                        async def _cleanup_prev() -> None:
                            try:
                                await bot.delete_message(chat_id, prev["message_id"])
                            except Exception:
                                pass
                            await delete_notification(prev["id"])

                        asyncio.create_task(_cleanup_prev())
                    msg = await bot.send_message(chat_id, text, reply_markup=vote_keyboard)
                    async with _notification_save_lock:
                        await save_notification(chat_id, msg.message_id)

                await broadcast_messages(subscribers, send_light)
        
        except Exception:
            logging.exception("sensors_monitor_loop error")
        
        await asyncio.sleep(CHECK_INTERVAL)
