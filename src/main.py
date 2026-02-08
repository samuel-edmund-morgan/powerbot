import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import CFG
from logging_setup import configure_logging

configure_logging("powerbot")

from database import init_db
from handlers import router
from services import alert_monitor_loop, sensors_monitor_loop
from yasno import yasno_schedule_monitor_loop
from api_server import create_api_app, start_api_server, stop_api_server
from single_message_bot import SingleMessageBot


async def main():
    """Точка входу в застосунок."""
    await init_db()

    bot_class = SingleMessageBot if CFG.single_message_mode else Bot
    bot = bot_class(
        token=CFG.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запускаємо API сервер для ESP32 сенсорів
    api_app = create_api_app()
    api_runner = await start_api_server(api_app)
    
    # Запускаємо фонові таски
    # Моніторинг ESP32 сенсорів (основна система визначення стану світла)
    asyncio.create_task(sensors_monitor_loop(bot))
    
    # Моніторинг тривог
    asyncio.create_task(alert_monitor_loop(bot))

    # Оновлення графіків ЯСНО + сповіщення про зміни
    if CFG.yasno_enabled:
        asyncio.create_task(yasno_schedule_monitor_loop(bot))
    
    try:
        await dp.start_polling(bot)
    finally:
        await stop_api_server(api_runner)


if __name__ == "__main__":
    asyncio.run(main())
