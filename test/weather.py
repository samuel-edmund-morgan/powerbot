"""Модуль для отримання погоди."""

import os
import aiohttp
import logging

# Координати за замовчуванням — центр Києва
# Можна змінити через WEATHER_LAT і WEATHER_LON в .env
WEATHER_LAT = float(os.getenv("WEATHER_LAT", "50.4501"))
WEATHER_LON = float(os.getenv("WEATHER_LON", "30.5234"))

# Open-Meteo API — безкоштовний, без ключа
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

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
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "current": "temperature_2m,weather_code",
        "timezone": "Europe/Kyiv",
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(WEATHER_API_URL, params=params, timeout=10) as resp:
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
    weather = await get_weather()
    if weather:
        return f"\n🌡 Погода: {weather}"
    return ""
