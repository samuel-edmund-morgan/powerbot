#!/usr/bin/env python3
"""
Telethon UAT: single-message regression check for resident bot.

Runs a short interactive script and verifies message growth from bot account
does not exceed configured threshold.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass


def _strip_quotes(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")


@dataclass
class Config:
    api_id: int
    api_hash: str
    session: str
    bot_username: str
    timeout_sec: int
    max_growth: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_id=int(_strip_quotes(os.getenv("TELETHON_API_ID", "0")) or 0),
            api_hash=_strip_quotes(os.getenv("TELETHON_API_HASH", "")),
            session=_strip_quotes(os.getenv("TESTERBOT_STRING_SESSION", "")),
            bot_username=_strip_quotes(os.getenv("TESTERBOT_TARGET_POWERBOT_USERNAME", "")).lstrip("@"),
            timeout_sec=int(_strip_quotes(os.getenv("SINGLE_MESSAGE_UAT_TIMEOUT_SEC", "25")) or 25),
            max_growth=int(_strip_quotes(os.getenv("SINGLE_MESSAGE_UAT_MAX_GROWTH", "3")) or 3),
        )


async def _wait_latest_bot_message(client, target, bot_id: int, timeout_sec: int):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        msgs = await client.get_messages(target, limit=12)
        for msg in msgs:
            if getattr(msg, "out", False):
                continue
            if int(getattr(msg, "sender_id", 0) or 0) != bot_id:
                continue
            text = str(getattr(msg, "message", "") or getattr(msg, "raw_text", "") or "").strip()
            if text:
                return msg, text
        await asyncio.sleep(0.8)
    raise TimeoutError("timeout waiting resident bot message")


async def _click_by_label(message, needle: str) -> bool:
    rows = getattr(message, "buttons", None) or []
    for i, row in enumerate(rows):
        for j, btn in enumerate(row):
            label = str(getattr(btn, "text", "") or "")
            if needle.casefold() in label.casefold():
                await message.click(i, j)
                return True
    return False


async def _run(cfg: Config) -> None:
    from telethon import TelegramClient  # type: ignore
    from telethon.sessions import StringSession  # type: ignore

    if cfg.api_id <= 0 or not cfg.api_hash or not cfg.session or not cfg.bot_username:
        raise SystemExit("ERROR: TELETHON_API_ID/TELETHON_API_HASH/TESTERBOT_STRING_SESSION/TESTERBOT_TARGET_POWERBOT_USERNAME required")

    client = TelegramClient(StringSession(cfg.session), cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("ERROR: provided Telethon session is not authorized")

    target = cfg.bot_username
    bot_id = await client.get_peer_id(target)
    started_wall = time.time()

    try:
        await client.send_message(target, "/start")
        msg, _ = await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)

        sequence = (
            "Світло/опалення/вода",
            "Меню",
            "Тривоги та укриття",
            "Меню",
            "Пошук закладу",
        )

        for step in sequence:
            clicked = await _click_by_label(msg, step)
            if not clicked:
                # non-fatal: interface can differ slightly between revisions
                continue
            msg, _ = await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)
            await asyncio.sleep(0.6)

        await client.send_message(target, "сирники")
        await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)
        await asyncio.sleep(0.8)

        incoming = await client.get_messages(target, limit=120)
        new_from_bot = [
            m
            for m in incoming
            if (not getattr(m, "out", False))
            and int(getattr(m, "sender_id", 0) or 0) == int(bot_id)
            and float(getattr(m, "date", 0).timestamp()) >= started_wall
        ]
        growth = len(new_from_bot)
        if growth > cfg.max_growth:
            raise SystemExit(
                f"ERROR: single-message UAT failed, bot message growth {growth} > {cfg.max_growth}"
            )

        print(f"OK: single-message UAT passed (growth={growth}, max={cfg.max_growth})")
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telethon UAT for resident single-message flow")
    parser.add_argument("--max-growth", type=int, default=None)
    args = parser.parse_args()

    cfg = Config.from_env()
    if args.max_growth is not None:
        cfg.max_growth = int(args.max_growth)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    main()

