import argparse
import asyncio
import sys
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

sys.path.append("/app/src")

from config import CFG
from alerts import alert_text
from services import broadcast_messages, format_light_status
from database import (
    get_subscribers_for_light_notification,
    get_subscribers_for_alert_notification,
    get_active_notifications,
    delete_notification,
    save_notification,
    get_last_bot_message,
    delete_last_bot_message_record,
)


def _build_vote_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="♨️ Є опалення", callback_data="vote_heating_yes"),
            InlineKeyboardButton(text="❄️ Немає", callback_data="vote_heating_no"),
        ],
        [
            InlineKeyboardButton(text="💧 Є вода", callback_data="vote_water_yes"),
            InlineKeyboardButton(text="🚫 Немає", callback_data="vote_water_no"),
        ],
        [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
    ])


def _build_alert_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")],
    ])


async def _send_light(
    bot: Bot,
    chat_id: int,
    existing_notifications: dict[int, dict],
    *,
    test_label: str | None,
) -> None:
    text = await format_light_status(chat_id, include_vote_prompt=False)
    if test_label:
        text = f"🧪 <b>ТЕСТ:</b> {test_label}\n\n{text}"

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

    msg = await bot.send_message(chat_id, text, reply_markup=_build_vote_keyboard())
    await save_notification(chat_id, msg.message_id)


async def _send_alert(
    bot: Bot,
    chat_id: int,
    existing_notifications: dict[int, dict],
    *,
    is_active: bool,
) -> None:
    text = alert_text(is_active)
    text = f"🧪 <b>ТЕСТ:</b> {'ТРИВОГА' if is_active else 'ВІДБІЙ'}\n\n{text}"

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

    msg = await bot.send_message(chat_id, text, reply_markup=_build_alert_keyboard())
    await save_notification(chat_id, msg.message_id, "alert")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate test notifications")
    parser.add_argument(
        "--mode",
        choices=("light_on", "light_off", "alert_on", "alert_off"),
        required=True,
    )
    parser.add_argument("--building-id", type=int, default=1)
    parser.add_argument(
        "--targets",
        choices=("admin", "subscribers"),
        default="admin",
        help="Whom to send to (admin IDs or real subscribers)",
    )
    args = parser.parse_args()

    bot = Bot(token=CFG.token, default=DefaultBotProperties(parse_mode="HTML"))
    current_hour = datetime.now().hour

    if args.targets == "admin":
        targets = list(CFG.admin_ids)
    else:
        if args.mode.startswith("light"):
            targets = await get_subscribers_for_light_notification(current_hour, args.building_id)
        else:
            targets = await get_subscribers_for_alert_notification(current_hour)

    if not targets:
        print("No targets to notify.")
        await bot.session.close()
        return

    if args.mode.startswith("light"):
        existing_notifications = {
            notif["chat_id"]: notif
            for notif in await get_active_notifications("power_change")
        }
        label = "СВІТЛО Є" if args.mode == "light_on" else "СВІТЛА НЕМАЄ"

        async def send(chat_id: int) -> None:
            await _send_light(
                bot,
                chat_id,
                existing_notifications,
                test_label=label,
            )

    else:
        existing_notifications = {
            notif["chat_id"]: notif
            for notif in await get_active_notifications("alert")
        }
        is_active = args.mode == "alert_on"

        async def send(chat_id: int) -> None:
            await _send_alert(
                bot,
                chat_id,
                existing_notifications,
                is_active=is_active,
            )

    await broadcast_messages(targets, send)
    await bot.session.close()
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
