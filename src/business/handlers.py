"""Handlers for standalone business bot (skeleton)."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Бізнес-кабінет готується.\n\n"
        "Функціонал буде відкриватися поетапно."
    )


@router.message(F.text == "/health")
async def cmd_health(message: Message) -> None:
    await message.answer("ok")
