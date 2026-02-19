#!/usr/bin/env python3
"""
Optional real Telegram E2E runner (Playwright-like for bot UI) via Telethon.

Runs a smoke scenario with a real Telegram account against test bot:
  /start -> Обрати будинок -> <building> -> <section> -> Світло/опалення/вода -> Світло

Env:
  TG_E2E_API_ID           - Telegram API ID (required)
  TG_E2E_API_HASH         - Telegram API HASH (required)
  TG_E2E_SESSION          - Telethon StringSession (required)
  TG_E2E_BOT_USERNAME     - bot username without @ (required)
  TG_E2E_BUILDING_LABEL   - default: "Ньюкасл (24-в)"
  TG_E2E_SECTION_LABEL    - default: "2 секція"
  TG_E2E_TIMEOUT_SEC      - default: 25

Example:
  TG_E2E_API_ID=... TG_E2E_API_HASH=... TG_E2E_SESSION=... \
  TG_E2E_BOT_USERNAME=powerbot_test_bot \
  python3 scripts/e2e_telegram_userbot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Iterable


def _require(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise SystemExit(f"ERROR: missing required env `{name}`")
    return value


def _load_telethon():
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "ERROR: telethon is required for this script.\n"
            "Install dev dependencies:\n"
            "  pip install -r requirements-dev.txt\n"
            f"Details: {exc}"
        )
    return TelegramClient, StringSession


@dataclass
class Cfg:
    api_id: int
    api_hash: str
    session: str
    bot_username: str
    building_label: str
    section_label: str
    timeout_sec: int


def _build_cfg() -> Cfg:
    return Cfg(
        api_id=int(_require("TG_E2E_API_ID")),
        api_hash=_require("TG_E2E_API_HASH"),
        session=_require("TG_E2E_SESSION"),
        bot_username=_require("TG_E2E_BOT_USERNAME").lstrip("@"),
        building_label=str(os.getenv("TG_E2E_BUILDING_LABEL", "Ньюкасл (24-в)")).strip(),
        section_label=str(os.getenv("TG_E2E_SECTION_LABEL", "2 секція")).strip(),
        timeout_sec=int(str(os.getenv("TG_E2E_TIMEOUT_SEC", "25")).strip()),
    )


def _assert_contains(text: str, tokens: Iterable[str], *, ctx: str) -> None:
    for token in tokens:
        if token not in text:
            raise AssertionError(f"{ctx}: expected `{token}` in:\n{text}")


def _find_button_coords(message, needle: str) -> tuple[int, int]:
    """
    Finds first inline button containing `needle` (case-insensitive).
    """
    buttons = getattr(message, "buttons", None) or []
    needle_norm = needle.casefold()
    for i, row in enumerate(buttons):
        for j, btn in enumerate(row):
            text = str(getattr(btn, "text", "")).strip()
            if needle_norm in text.casefold():
                return i, j
    available = []
    for row in buttons:
        for btn in row:
            available.append(str(getattr(btn, "text", "")).strip())
    raise AssertionError(
        f"button containing `{needle}` not found. Available buttons: {available}"
    )


async def _wait_bot_update(conv, timeout_sec: int):
    """
    Wait for either edited message or new response.
    """
    edit_task = asyncio.create_task(conv.get_edit(timeout=timeout_sec))
    resp_task = asyncio.create_task(conv.get_response(timeout=timeout_sec))
    done, pending = await asyncio.wait(
        {edit_task, resp_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        return task.result()
    raise AssertionError("No update received from bot")


async def _click_and_wait(conv, message, label_contains: str, timeout_sec: int):
    i, j = _find_button_coords(message, label_contains)
    await message.click(i, j)
    return await _wait_bot_update(conv, timeout_sec)


async def run_smoke(cfg: Cfg) -> None:
    TelegramClient, StringSession = _load_telethon()
    client = TelegramClient(StringSession(cfg.session), cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit(
            "ERROR: session is not authorized. Create fresh StringSession for your test account."
        )

    try:
        async with client.conversation(cfg.bot_username, timeout=cfg.timeout_sec) as conv:
            await conv.send_message("/start")
            msg = await _wait_bot_update(conv, cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Головне меню",), ctx="after /start")

            msg = await _click_and_wait(conv, msg, "Обрати будинок", cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Оберіть ваш будинок",), ctx="building menu")

            msg = await _click_and_wait(conv, msg, cfg.building_label, cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Оберіть секцію",), ctx="section menu")

            msg = await _click_and_wait(conv, msg, cfg.section_label, cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Секцію", "збережено"), ctx="section saved")

            msg = await _click_and_wait(conv, msg, "Світло/опалення/вода", cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Світло / Опалення / Вода",), ctx="utilities menu")

            msg = await _click_and_wait(conv, msg, "Світло", cfg.timeout_sec)
            _assert_contains(str(msg.raw_text or ""), ("Стан електропостачання",), ctx="light status")

        print("OK: Telegram E2E resident smoke passed.")
    finally:
        await client.disconnect()


def main() -> None:
    cfg = _build_cfg()
    asyncio.run(run_smoke(cfg))


if __name__ == "__main__":
    main()

