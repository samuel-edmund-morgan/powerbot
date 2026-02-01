import argparse
import asyncio
import sys
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

sys.path.append("/app/src")

from config import CFG
from single_message_bot import SingleMessageBot
from database import (
    list_subscribers,
    get_active_notifications,
    delete_notification,
    save_notification,
    get_last_bot_message,
    delete_last_bot_message_record,
)
from services import broadcast_messages


async def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate broadcast notification")
    parser.add_argument(
        "--targets",
        choices=("admin", "subscribers"),
        default="admin",
        help="Whom to send to (admin IDs or real subscribers)",
    )
    parser.add_argument(
        "--text",
        default="",
        help="Broadcast text (if empty, uses a timestamped test message)",
    )
    args = parser.parse_args()

    bot_class = SingleMessageBot if CFG.single_message_mode else Bot
    bot = bot_class(token=CFG.token, default=DefaultBotProperties(parse_mode="HTML"))

    if args.targets == "admin":
        targets = list(CFG.admin_ids)
    else:
        targets = await list_subscribers()

    if not targets:
        print("No targets to notify.")
        await bot.session.close()
        return

    stamp = datetime.now().strftime("%H:%M:%S")
    text = args.text.strip() or f"🧪 <b>ТЕСТ:</b> BROADCAST {stamp}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
    )

    existing_notifications = {
        notif["chat_id"]: notif
        for notif in await get_active_notifications("broadcast")
    }

    async def send(chat_id: int) -> None:
        if not CFG.single_message_mode:
            last_menu_id = await get_last_bot_message(chat_id)
            if last_menu_id:
                try:
                    await bot.delete_message(chat_id, last_menu_id)
                except Exception:
                    pass
                await delete_last_bot_message_record(chat_id)

            prev = existing_notifications.get(chat_id)
            if prev:
                try:
                    await bot.delete_message(chat_id, prev["message_id"])
                except Exception:
                    pass
                await delete_notification(prev["id"])

        msg = await bot.send_message(chat_id, f"📢 {text}", reply_markup=keyboard)
        await save_notification(chat_id, msg.message_id, "broadcast")

    await broadcast_messages(targets, send)
    await bot.session.close()
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
