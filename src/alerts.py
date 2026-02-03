"""
Сервіс моніторингу тривог через два API джерела:
1. ukrainealarm.com - основне джерело
2. alerts.in.ua - резервне/додаткове джерело

Диверсифікація запитів для уникнення rate limit та блокувань.
Алгоритм:
- джерела опитуються з ротацією (alerts.in.ua частіше)
- якщо хоча б одне джерело дає тривогу -> тривога
- відбій лише тоді, коли обидва джерела підтвердили відбій
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from enum import Enum

import aiohttp

from config import CFG

logger = logging.getLogger(__name__)


class AlertSource(Enum):
    """Джерела тривог."""
    UKRAINEALARM = "ukrainealarm"
    ALERTS_IN_UA = "alerts_in_ua"


class AlertStatus:
    """Статус тривоги."""
    ACTIVE = "active"      # Тривога оголошена
    INACTIVE = "inactive"  # Відбій тривоги


# Лічильник запитів для балансування джерел
# alerts.in.ua оновлюється кожні 15 сек, ukrainealarm має суворіший rate limit
_request_counter = 0
# alerts.in.ua як основне джерело, ukrainealarm як рідший резерв
ALERTS_IN_UA_RATIO = max(0, CFG.alerts_in_ua_ratio)

# Останні відомі результати по кожному джерелу
_last_status: dict[AlertSource, Optional[bool]] = {
    AlertSource.UKRAINEALARM: None,
    AlertSource.ALERTS_IN_UA: None,
}


def _get_enabled_sources() -> list[AlertSource]:
    """Повернути список увімкнених джерел (за наявністю API ключів)."""
    sources: list[AlertSource] = []
    if CFG.alerts_in_ua_api_key:
        sources.append(AlertSource.ALERTS_IN_UA)
    if CFG.alerts_api_key:
        sources.append(AlertSource.UKRAINEALARM)
    return sources


def _get_next_source() -> AlertSource:
    """
    Отримати наступне джерело для запиту.
    Пріоритет: alerts.in.ua (ALERTS_IN_UA_RATIO з ALERTS_IN_UA_RATIO + 1 запитів).
    Якщо увімкнене лише одне джерело - повертаємо його.
    """
    enabled = _get_enabled_sources()
    if not enabled:
        return AlertSource.ALERTS_IN_UA
    if len(enabled) == 1:
        return enabled[0]

    global _request_counter
    _request_counter += 1

    # Кожен (ALERTS_IN_UA_RATIO + 1)-й запит до ukrainealarm, решта до alerts.in.ua
    if _request_counter % (ALERTS_IN_UA_RATIO + 1) == 0:
        return AlertSource.UKRAINEALARM
    return AlertSource.ALERTS_IN_UA


def _record_status(source: AlertSource, status: bool) -> None:
    """
    Зберегти результат джерела.
    Якщо джерело дало тривогу - скидаємо статус інших, щоб відбій
    вимагав повторного підтвердження від обох.
    """
    _last_status[source] = status
    if status is True:
        for other in AlertSource:
            if other != source:
                _last_status[other] = None


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
            url = f"{CFG.alerts_api_url}/alerts/{CFG.alerts_city_id_ukrainealarm}"
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
            url = f"{CFG.alerts_in_ua_api_url}/iot/active_air_raid_alerts/{CFG.alerts_city_uid_alerts_in_ua}.json"
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
    - Якщо хоча б одне джерело дало тривогу -> тривога
    - Відбій лише тоді, коли обидва джерела підтвердили відбій
    - Якщо обидва недоступні або немає підтвердження - None
    
    Returns:
        True - тривога активна
        False - відбій
        None - обидва джерела недоступні
    """
    enabled = _get_enabled_sources()
    if not enabled:
        logger.debug("Немає налаштованих джерел тривог")
        return None

    source = _get_next_source()

    # Оновлюємо статус лише для джерела, яке опитали
    result = await check_alert_status_single(source)
    if result is not None:
        _record_status(source, result)
    elif len(enabled) > 1:
        # Якщо основне недоступне - пробуємо інше
        other_source = AlertSource.ALERTS_IN_UA if source == AlertSource.UKRAINEALARM else AlertSource.UKRAINEALARM
        logger.info(f"Джерело {source.value} недоступне, пробуємо {other_source.value}")
        other_result = await check_alert_status_single(other_source)
        if other_result is not None:
            _record_status(other_source, other_result)

    # Якщо увімкнене лише одне джерело - довіряємо йому
    if len(enabled) == 1:
        return result

    # Якщо хоча б одне джерело активне - тривога
    if any(_last_status.get(src) is True for src in enabled):
        return True

    # Відбій лише якщо обидва джерела підтвердили відбій
    if all(_last_status.get(src) is False for src in enabled):
        return False

    return None


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
