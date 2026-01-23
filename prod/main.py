import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import CFG
from database import init_db
from handlers import router
from services import monitor_loop, alert_monitor_loop, sensors_monitor_loop
from api_server import create_api_app, start_api_server, stop_api_server

logging.basicConfig(level=logging.INFO)


async def main():
    """Точка входу в застосунок."""
    await init_db()

    bot = Bot(
        token=CFG.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Запускаємо API сервер для ESP32 сенсорів
    api_app = create_api_app()
    api_runner = await start_api_server(api_app)
    
    # Запускаємо фонові таски
    # Стара система моніторингу (пінг IP) - для зворотної сумісності
    if CFG.home_ips:
        asyncio.create_task(monitor_loop(bot))
    
    # Нова система моніторингу (ESP32 сенсори)
    asyncio.create_task(sensors_monitor_loop(bot))
    
    # Моніторинг тривог
    asyncio.create_task(alert_monitor_loop(bot))
    
    try:
        await dp.start_polling(bot)
    finally:
        await stop_api_server(api_runner)


if __name__ == "__main__":
    asyncio.run(main())
