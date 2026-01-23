"""
Сервіс моніторингу тривог через два API джерела:
1. ukrainealarm.com - основне джерело
2. alerts.in.ua - резервне/додаткове джерело

Диверсифікація запитів для уникнення rate limit та блокувань.
Алгоритм: чергування джерел, якщо хоч одне дає тривогу - тривога.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from enum import Enum

import aiohttp

from config import CFG

# ID міста Київ
KYIV_CITY_ID_UKRAINEALARM = "31"  # для ukrainealarm.com
KYIV_CITY_UID_ALERTS_IN_UA = "31"  # для alerts.in.ua (м. Київ)

# URLs API
UKRAINEALARM_API_URL = "https://api.ukrainealarm.com/api/v3"
ALERTS_IN_UA_API_URL = "https://api.alerts.in.ua/v1"

logger = logging.getLogger(__name__)


class AlertSource(Enum):
    """Джерела тривог."""
    UKRAINEALARM = "ukrainealarm"
    ALERTS_IN_UA = "alerts_in_ua"


class AlertStatus:
    """Статус тривоги."""
    ACTIVE = "active"      # Тривога оголошена
    INACTIVE = "inactive"  # Відбій тривоги


# Поточне джерело для чергування
_current_source_index = 0
_sources = [AlertSource.UKRAINEALARM, AlertSource.ALERTS_IN_UA]


def _get_next_source() -> AlertSource:
    """Отримати наступне джерело для запиту (чергування)."""
    global _current_source_index
    source = _sources[_current_source_index]
    _current_source_index = (_current_source_index + 1) % len(_sources)
    return source


async def get_kyiv_alerts_ukrainealarm() -> Optional[bool]:
    """
    Отримати статус тривоги для Києва з ukrainealarm.com.
    
    Returns:
        True - тривога активна
        False - відбій
        None - помилка запиту
    """
    if not CFG.alerts_api_key:
        logger.debug("ALERTS_API_KEY не налаштовано")
        return None
    
    headers = {
        "Authorization": CFG.alerts_api_key,
        "Accept": "application/json",
        "User-Agent": "PowerBot/1.0"
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{UKRAINEALARM_API_URL}/alerts/{KYIV_CITY_ID_UKRAINEALARM}"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # API повертає список з об'єктами
                    if isinstance(data, list) and len(data) > 0:
                        for alert in data:
                            if "activeAlerts" in alert and len(alert["activeAlerts"]) > 0:
                                logger.info("ukrainealarm: ТРИВОГА активна")
                                return True
                    logger.info("ukrainealarm: відбій")
                    return False
                elif resp.status == 401:
                    logger.warning("ukrainealarm: 401 Unauthorized (rate limit?)")
                    return None
                elif resp.status == 429:
                    logger.warning("ukrainealarm: 429 Too Many Requests")
                    return None
                else:
                    logger.warning(f"ukrainealarm: статус {resp.status}")
                    return None
    except asyncio.TimeoutError:
        logger.error("ukrainealarm: таймаут")
        return None
    except Exception as e:
        logger.error(f"ukrainealarm: помилка {e}")
        return None


async def get_kyiv_alerts_in_ua() -> Optional[bool]:
    """
    Отримати статус тривоги для Києва з alerts.in.ua.
    
    Використовує IoT endpoint /v1/iot/active_air_raid_alerts/{uid}.json
    який повертає "A" (тривога), "P" (часткова) або "N" (немає).
    
    Returns:
        True - тривога активна (A або P)
        False - відбій (N)
        None - помилка запиту
    """
    if not CFG.alerts_in_ua_api_key:
        logger.debug("ALERTS_IN_UA_API_KEY не налаштовано")
        return None
    
    headers = {
        "Authorization": f"Bearer {CFG.alerts_in_ua_api_key}",
        "Accept": "application/json",
        "User-Agent": "PowerBot/1.0"
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # IoT endpoint для конкретного регіону
            url = f"{ALERTS_IN_UA_API_URL}/iot/active_air_raid_alerts/{KYIV_CITY_UID_ALERTS_IN_UA}.json"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    # Відповідь - просто рядок "A", "P" або "N"
                    text = await resp.text()
                    text = text.strip().strip('"')
                    
                    if text == "A":
                        logger.info("alerts.in.ua: ТРИВОГА активна (A)")
                        return True
                    elif text == "P":
                        logger.info("alerts.in.ua: часткова тривога (P)")
                        return True  # Теж вважаємо тривогою
                    elif text == "N":
                        logger.info("alerts.in.ua: відбій (N)")
                        return False
                    else:
                        logger.warning(f"alerts.in.ua: невідомий статус '{text}'")
                        return None
                elif resp.status == 401:
                    logger.warning("alerts.in.ua: 401 Unauthorized")
                    return None
                elif resp.status == 429:
                    logger.warning("alerts.in.ua: 429 Too Many Requests")
                    return None
                elif resp.status == 304:
                    # Not Modified - дані не змінились
                    logger.debug("alerts.in.ua: 304 Not Modified")
                    return None
                else:
                    logger.warning(f"alerts.in.ua: статус {resp.status}")
                    return None
    except asyncio.TimeoutError:
        logger.error("alerts.in.ua: таймаут")
        return None
    except Exception as e:
        logger.error(f"alerts.in.ua: помилка {e}")
        return None


async def check_alert_status_single(source: AlertSource) -> Optional[bool]:
    """
    Перевірити статус тривоги з конкретного джерела.
    
    Args:
        source: джерело для запиту
        
    Returns:
        True - тривога активна
        False - відбій
        None - помилка запиту
    """
    if source == AlertSource.UKRAINEALARM:
        return await get_kyiv_alerts_ukrainealarm()
    elif source == AlertSource.ALERTS_IN_UA:
        return await get_kyiv_alerts_in_ua()
    return None


async def check_alert_status() -> Optional[bool]:
    """
    Перевірити статус тривоги з чергуванням джерел.
    
    Алгоритм:
    - Запитуємо по черзі то одне, то інше джерело
    - Якщо джерело повернуло None (помилка) - пробуємо інше
    - Результат повертаємо з першого успішного джерела
    
    Returns:
        True - тривога активна
        False - відбій
        None - обидва джерела недоступні
    """
    source = _get_next_source()
    
    # Пробуємо основне джерело
    result = await check_alert_status_single(source)
    if result is not None:
        return result
    
    # Якщо основне не спрацювало - пробуємо резервне
    other_source = AlertSource.ALERTS_IN_UA if source == AlertSource.UKRAINEALARM else AlertSource.UKRAINEALARM
    logger.info(f"Джерело {source.value} недоступне, пробуємо {other_source.value}")
    
    result = await check_alert_status_single(other_source)
    return result


def alert_text(is_active: bool) -> str:
    """
    Текстове представлення стану тривоги.
    
    Args:
        is_active: True якщо тривога активна
    """
    if is_active:
        return (
            "🚨 <b>ПОВІТРЯНА ТРИВОГА!</b>\n\n"
            "⚠️ Оголошено повітряну тривогу в місті Київ.\n"
            "🏃 Прямуйте до найближчого укриття!"
        )
    else:
        return (
            "✅ <b>ВІДБІЙ ТРИВОГИ</b>\n\n"
            "Повітряну тривогу в місті Київ скасовано.\n"
            "🏠 Можна повертатися з укриття."
        )


def alert_status_short(is_active: bool) -> str:
    """Короткий статус тривоги."""
    if is_active:
        return "🚨 Тривога!"
    else:
        return "✅ Без тривоги"
