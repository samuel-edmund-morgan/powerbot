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
    min_clicks: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_id=int(_strip_quotes(os.getenv("TELETHON_API_ID", "0")) or 0),
            api_hash=_strip_quotes(os.getenv("TELETHON_API_HASH", "")),
            session=_strip_quotes(os.getenv("TESTERBOT_STRING_SESSION", "")),
            bot_username=_strip_quotes(os.getenv("TESTERBOT_TARGET_POWERBOT_USERNAME", "")).lstrip("@"),
            timeout_sec=int(_strip_quotes(os.getenv("SINGLE_MESSAGE_UAT_TIMEOUT_SEC", "25")) or 25),
            max_growth=int(_strip_quotes(os.getenv("SINGLE_MESSAGE_UAT_MAX_GROWTH", "3")) or 3),
            min_clicks=int(_strip_quotes(os.getenv("SINGLE_MESSAGE_UAT_MIN_CLICKS", "50")) or 50),
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
                try:
                    await message.click(i, j)
                    return True
                except Exception as exc:
                    # Callback payload may become stale during rapid single-message edits.
                    # Treat these errors as a soft miss and let caller recover with /start.
                    if exc.__class__.__name__ in {"DataInvalidError", "MessageIdInvalidError"}:
                        await asyncio.sleep(0.25)
                        return False
                    raise
    return False


async def _click_first_available(message, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        if await _click_by_label(message, label):
            return label
    return None


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

        # Hard acceptance: drive 50+ interactive clicks and ensure it still behaves
        # as single-message UI (minimal message growth).
        clicks_done = 0
        recoveries_used = 0
        iteration_budget = max(cfg.min_clicks, 1) * 6
        while clicks_done < max(cfg.min_clicks, 1) and iteration_budget > 0:
            iteration_budget -= 1
            # Prefer deterministic oscillation: main-menu -> light submenu -> menu.
            clicked_label = await _click_first_available(
                msg,
                (
                    "Світло/опалення/вода",
                    "Світло",
                    "Тривоги та укриття",
                    "Заклади в ЖК",
                    "Обрати будинок",
                    "Сервісна служба",
                    "Пошук закладу",
                    "Меню",
                    "« Меню",
                    "« Назад",
                    "Головне меню",
                ),
            )
            if not clicked_label:
                # Recovery path: force main menu and continue.
                recoveries_used += 1
                if recoveries_used > 10:
                    raise SystemExit(
                        "ERROR: single-message UAT cannot recover navigation controls "
                        f"(clicks_done={clicks_done}, recoveries={recoveries_used})"
                    )
                await client.send_message(target, "/start")
                msg, _ = await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)
                await asyncio.sleep(0.8)
                continue
            clicks_done += 1
            msg, _ = await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)
            await asyncio.sleep(0.6)

        if clicks_done < max(cfg.min_clicks, 1):
            raise SystemExit(
                "ERROR: single-message UAT did not reach required clicks "
                f"(clicks_done={clicks_done}, required={cfg.min_clicks})"
            )

        # Text-input branch should also stay in single-message pattern.
        if await _click_by_label(msg, "Пошук закладу"):
            msg, _ = await _wait_latest_bot_message(client, target, bot_id, cfg.timeout_sec)
            await asyncio.sleep(0.5)
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

        print(
            f"OK: single-message UAT passed (clicks={clicks_done}, growth={growth}, max={cfg.max_growth})"
        )
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telethon UAT for resident single-message flow")
    parser.add_argument("--max-growth", type=int, default=None)
    parser.add_argument("--min-clicks", type=int, default=None)
    args = parser.parse_args()

    cfg = Config.from_env()
    if args.max_growth is not None:
        cfg.max_growth = int(args.max_growth)
    if args.min_clicks is not None:
        cfg.min_clicks = int(args.min_clicks)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    main()
