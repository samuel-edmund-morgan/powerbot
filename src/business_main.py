import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import CFG, is_business_bot_enabled
from logging_setup import configure_logging
from database import init_db

configure_logging("businessbot")

from business.handlers import router

logger = logging.getLogger(__name__)


async def main() -> None:
    """Entry point for standalone business bot runtime."""
    if not is_business_bot_enabled():
        logger.warning(
            "Business bot disabled: requires BUSINESS_MODE=1 and non-empty BUSINESS_BOT_API_KEY."
        )
        return

    await init_db()

    bot = Bot(
        token=CFG.business_bot_api_key,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
