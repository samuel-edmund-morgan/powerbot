"""Модуль для отримання погоди."""

import aiohttp
import json
import logging
import time

from config import CFG
from database import db_get, db_set

# Коди погоди WMO -> текст українською
WMO_CODES = {
    0: "☀️ ясно",
    1: "🌤 переважно ясно",
    2: "⛅ мінлива хмарність",
    3: "☁️ хмарно",
    45: "🌫 туман",
    48: "🌫 паморозь",
    51: "🌧 мряка",
    53: "🌧 мряка",
    55: "🌧 сильна мряка",
    56: "🌧 крижана мряка",
    57: "🌧 сильна крижана мряка",
    61: "🌧 невеликий дощ",
    63: "🌧 дощ",
    65: "🌧 сильний дощ",
    66: "🌧 крижаний дощ",
    67: "🌧 сильний крижаний дощ",
    71: "🌨 невеликий сніг",
    73: "🌨 сніг",
    75: "🌨 сильний сніг",
    77: "🌨 снігові зерна",
    80: "🌧 невеликі зливи",
    81: "🌧 зливи",
    82: "🌧 сильні зливи",
    85: "🌨 невеликий снігопад",
    86: "🌨 снігопад",
    95: "⛈ гроза",
    96: "⛈ гроза з градом",
    99: "⛈ сильна гроза з градом",
}


async def get_weather() -> str | None:
    """
    Отримати поточну погоду для Києва.
    
    Returns:
        Рядок з погодою або None при помилці
    """
    params = {
        "latitude": CFG.weather_lat,
        "longitude": CFG.weather_lon,
        "current": "temperature_2m,weather_code",
        "timezone": CFG.weather_timezone,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CFG.weather_api_url, params=params, timeout=3) as resp:
                if resp.status != 200:
                    logging.warning("Weather API returned %s", resp.status)
                    return None
                
                data = await resp.json()
                
                current = data.get("current", {})
                temp = current.get("temperature_2m")
                code = current.get("weather_code")
                
                if temp is None:
                    return None
                
                # Округлюємо температуру
                temp_str = f"{temp:+.0f}°C" if temp != 0 else "0°C"
                
                # Опис погоди
                description = WMO_CODES.get(code, "")
                
                if description:
                    return f"{temp_str}, {description}"
                else:
                    return temp_str
                    
    except Exception as e:
        logging.warning("Failed to get weather: %s", e)
        return None


async def get_weather_line() -> str:
    """
    Отримати рядок з погодою для сповіщення.
    Повертає порожній рядок при помилці.
    """
    now = time.time()
    if now - _WEATHER_CACHE["ts"] < _WEATHER_CACHE_TTL:
        return _WEATHER_CACHE["value"]

    cached_line = ""
    cached_ts = 0.0
    cached_raw = await db_get(_WEATHER_CACHE_KEY)
    if cached_raw:
        try:
            cached = json.loads(cached_raw)
            cached_ts = float(cached.get("ts") or 0)
            cached_line = cached.get("value") or ""
        except Exception:
            cached_line = ""
            cached_ts = 0.0

    if cached_line and now - cached_ts < _WEATHER_CACHE_TTL:
        _WEATHER_CACHE["ts"] = now
        _WEATHER_CACHE["value"] = cached_line
        return cached_line

    weather = await get_weather()
    if weather:
        line = f"\n🌡 Погода: {weather}"
        payload = json.dumps({"ts": now, "value": line})
        await db_set(_WEATHER_CACHE_KEY, payload)
        _WEATHER_CACHE["ts"] = now
        _WEATHER_CACHE["value"] = line
        return line

    if cached_line:
        _WEATHER_CACHE["ts"] = now
        _WEATHER_CACHE["value"] = cached_line
        return cached_line

    _WEATHER_CACHE["ts"] = now
    _WEATHER_CACHE["value"] = ""
    return ""


_WEATHER_CACHE_TTL = 3600  # 1 година
_WEATHER_CACHE = {"ts": 0.0, "value": ""}
_WEATHER_CACHE_KEY = "weather_cache_v1"
