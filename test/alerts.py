"""
Сервіс моніторингу тривог через API ukrainealarm.com
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiohttp

from config import CFG

# ID міста Київ (не Київська область!)
KYIV_CITY_ID = "31"

# URL API
API_BASE_URL = "https://api.ukrainealarm.com/api/v3"

logger = logging.getLogger(__name__)


class AlertStatus:
    """Статус тривоги."""
    ACTIVE = "active"      # Тривога оголошена
    INACTIVE = "inactive"  # Відбій тривоги


async def get_kyiv_alerts() -> Optional[dict]:
    """
    Отримати статус тривоги для міста Київ.
    
    Returns:
        dict з інформацією про тривогу або None при помилці
    """
    if not CFG.alerts_api_key:
        logger.warning("ALERTS_API_KEY не налаштовано")
        return None
    
    headers = {
        "Authorization": CFG.alerts_api_key,
        "Accept": "application/json",
        "User-Agent": "PowerBot/1.0"
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Отримуємо статус тривоги для Києва
            url = f"{API_BASE_URL}/alerts/{KYIV_CITY_ID}"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    logger.warning(f"API повернув статус {resp.status}")
                    return None
    except asyncio.TimeoutError:
        logger.error("Таймаут при запиті до API тривог")
        return None
    except Exception as e:
        logger.error(f"Помилка запиту до API тривог: {e}")
        return None


async def check_alert_status() -> Optional[bool]:
    """
    Перевірити чи є активна тривога в Києві.
    
    Returns:
        True - тривога активна
        False - відбій
        None - помилка запиту
    """
    data = await get_kyiv_alerts()
    
    if data is None:
        return None
    
    # API повертає список активних тривог
    # Якщо список не пустий - є тривога
    if isinstance(data, list) and len(data) > 0:
        # Перевіряємо чи є активні тривоги
        for alert in data:
            if "activeAlerts" in alert and len(alert["activeAlerts"]) > 0:
                return True
        return False
    
    return False


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
