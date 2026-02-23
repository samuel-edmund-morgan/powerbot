"""Business bot smoke scenario."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import click_button_and_wait, extract_text

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    name: str
    status: str
    duration_ms: int
    message: str


def _has_button(message, needle: str) -> bool:
    needle_norm = needle.casefold()
    buttons = getattr(message, "buttons", None) or []
    for row in buttons:
        for btn in row:
            text = str(getattr(btn, "text", "")).strip()
            if needle_norm in text.casefold():
                return True
    return False


async def _ensure_main_menu(conv, message, timeout_sec: int):
    current = message
    for _ in range(3):
        text = extract_text(current)
        if "Оберіть дію:" in text:
            return current
        if _has_button(current, "Меню"):
            current = await click_button_and_wait(conv, current, "Меню", timeout_sec)
            continue
        if _has_button(current, "Назад"):
            current = await click_button_and_wait(conv, current, "Назад", timeout_sec)
            continue
        break
    text = extract_text(current)
    assert_contains(text, ("Оберіть дію:",), ctx="business ensure main menu")
    return current


async def run(ctx) -> ScenarioResult:
    """Open business bot and verify key menu screens without data mutations."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.businessbot, timeout=ctx.cfg.timeout_sec) as conv:
        async def settle(message):
            text_local = extract_text(message)
            if text_local.strip() not in {"…", "...", "Оновлюю меню…"}:
                return message, text_local
            for _ in range(5):
                message = await ctx.wait_msg(conv)
                text_local = extract_text(message)
                if text_local.strip() not in {"…", "...", "Оновлюю меню…"}:
                    break
            return message, text_local

        async def click_and_settle(message, needle: str):
            message = await click_button_and_wait(conv, message, needle, ctx.cfg.timeout_sec)
            return await settle(message)

        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        msg, text = await settle(msg)
        assert_contains(text, ("Бізнес-кабінет",), ctx="business /start")
        assert_contains(text, ("Оберіть дію:",), ctx="business /start action prompt")

        msg, text = await click_and_settle(msg, "🏢 Мої бізнеси")
        # If owner has no business, bot still should open a valid flow.
        assert_contains_any(
            text,
            ("Оберіть заклад", "У тебе ще немає бізнесів"),
            ctx="business my places",
        )

        msg = await _ensure_main_menu(conv, msg, ctx.cfg.timeout_sec)
        msg, _ = await settle(msg)

        msg, text = await click_and_settle(msg, "💳 Плани")
        assert_contains_any(
            text,
            ("Плани", "Немає підтверджених закладів"),
            ctx="business plans",
        )

        msg = await _ensure_main_menu(conv, msg, ctx.cfg.timeout_sec)
        msg, _ = await settle(msg)

        # Start add-flow and cancel immediately (idempotent flow smoke).
        msg, text = await click_and_settle(msg, "Додати бізнес")
        assert_contains_any(
            text,
            ("Оберіть категорію", "Немає жодної категорії"),
            ctx="business add menu",
        )
        if _has_button(msg, "Скасувати"):
            msg, text = await click_and_settle(msg, "Скасувати")
            assert_contains(text, ("Оберіть дію:",), ctx="business add cancel")
        else:
            assert_contains(text, ("Оберіть дію:",), ctx="business add fallback menu")

        # Start attach-flow and cancel immediately (idempotent flow smoke).
        msg, text = await click_and_settle(msg, "Прив'язати бізнес")
        assert_contains(text, ("Введи код прив'язки",), ctx="business attach menu")
        msg, text = await click_and_settle(msg, "Скасувати")
        assert_contains(text, ("Оберіть дію:",), ctx="business attach cancel")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("business scenario completed in %sms", elapsed)
    return ScenarioResult(name="business", status="ok", duration_ms=elapsed, message="passed")
