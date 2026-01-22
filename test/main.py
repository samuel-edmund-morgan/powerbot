import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import CFG
from database import init_db
from handlers import router
from services import monitor_loop, alert_monitor_loop

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

    asyncio.create_task(monitor_loop(bot))
    asyncio.create_task(alert_monitor_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
