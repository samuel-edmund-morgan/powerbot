"""Resident bot smoke scenarios."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import extract_text, find_button

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    name: str
    status: str
    duration_ms: int
    message: str


def _is_nav_button(label: str) -> bool:
    normalized = label.casefold()
    return ("меню" in normalized) or ("назад" in normalized)


def _has_button(message, needle: str) -> bool:
    try:
        find_button(message, needle)
        return True
    except AssertionError:
        return False


async def run(ctx) -> ScenarioResult:
    """Resident path (stable): /start -> building menu -> alerts -> places -> search."""
    started = time.perf_counter()
    target = ctx.cfg.targets.powerbot
    bot_id = await ctx.client.get_peer_id(target)

    async def wait_bot_message(*, predicate, ctx_name: str):
        deadline = time.monotonic() + ctx.cfg.timeout_sec
        last_text = ""
        while time.monotonic() < deadline:
            msgs = await ctx.client.get_messages(target, limit=10)
            for msg in msgs:
                if getattr(msg, "out", False):
                    continue
                if getattr(msg, "sender_id", None) != bot_id:
                    continue
                text = extract_text(msg)
                if not text:
                    continue
                last_text = text
                if predicate(msg, text):
                    return msg, text
            await asyncio.sleep(0.6)
        raise AssertionError(f"{ctx_name}: timeout waiting bot message. last_text=\n{last_text}")

    async def click_and_wait(message, needle: str, *, predicate, ctx_name: str):
        i, j = find_button(message, needle)
        await message.click(i, j)
        return await wait_bot_message(predicate=predicate, ctx_name=ctx_name)

    await ctx.client.send_message(target, "/start")
    msg, text = await wait_bot_message(
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Обрати будинок"),
        ctx_name="resident /start",
    )
    assert_contains(text, ("Головне меню",), ctx="resident /start")

    msg, text = await click_and_wait(
        msg,
        "Обрати будинок",
        predicate=lambda _m, t: ("Оберіть свій будинок" in t) or ("Оберіть ваш будинок" in t),
        ctx_name="resident buildings",
    )
    assert_contains_any(
        text,
        ("Оберіть ваш будинок", "Оберіть свій будинок"),
        ctx="resident buildings",
    )

    # Return to main menu without mutating building/section selection.
    msg, text = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
        ctx_name="resident back to menu",
    )
    assert_contains(text, ("Головне меню",), ctx="resident back to menu")

    # Alerts flow.
    msg, text = await click_and_wait(
        msg,
        "Тривоги та укриття",
        predicate=lambda _m, t: "Тривоги та укриття" in t,
        ctx_name="resident alerts menu",
    )
    assert_contains(text, ("Тривоги та укриття",), ctx="resident alerts menu")

    msg, text = await click_and_wait(
        msg,
        "Стан тривоги",
        predicate=lambda _m, t: (
            ("ПОВІТРЯНА ТРИВОГА" in t) or ("Відбій тривоги" in t) or ("Статус невідомий" in t)
        ),
        ctx_name="resident alert status",
    )
    assert_contains_any(
        text,
        ("ПОВІТРЯНА ТРИВОГА", "Відбій тривоги", "Статус невідомий"),
        ctx="resident alert status",
    )

    msg, _ = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Заклади в ЖК"),
        ctx_name="resident alerts back to menu",
    )

    # Places flow (read-only).
    msg, text = await click_and_wait(
        msg,
        "Заклади в ЖК",
        predicate=lambda _m, t: "Заклади в ЖК" in t,
        ctx_name="resident places menu",
    )
    assert_contains(text, ("Заклади в ЖК",), ctx="resident places menu")
    assert_contains_any(text, ("Оберіть категорію", "Поки що категорій немає"), ctx="resident places categories")

    msg, _ = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
        ctx_name="resident places back to menu",
    )

    # Search flow.
    msg, text = await click_and_wait(
        msg,
        "Пошук закладу",
        predicate=lambda _m, t: "Пошук закладів" in t,
        ctx_name="resident search menu",
    )
    assert_contains(text, ("Пошук закладів",), ctx="resident search menu")

    await ctx.client.send_message(target, "сирники")
    _, text = await wait_bot_message(
        predicate=lambda _m, t: (
            ("Результати пошуку" in t) or ("нічого не знайдено" in t) or ("нічого не знайдено." in t)
        ),
        ctx_name="resident search result",
    )
    assert_contains_any(
        text,
        ("Результати пошуку", "нічого не знайдено", "нічого не знайдено."),
        ctx="resident search result",
    )

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("resident scenario completed in %sms", elapsed)
    return ScenarioResult(name="resident_powerbot", status="ok", duration_ms=elapsed, message="passed")
