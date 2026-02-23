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


async def run(ctx) -> ScenarioResult:
    """Open business bot and verify key menu screens without data mutations."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.businessbot, timeout=ctx.cfg.timeout_sec) as conv:
        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес-кабінет",), ctx="business /start")
        assert_contains(text, ("Оберіть дію:",), ctx="business /start action prompt")

        msg = await click_button_and_wait(conv, msg, "🏢 Мої бізнеси", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        # If owner has no business, bot still should open a valid flow.
        assert_contains_any(
            text,
            ("Оберіть заклад", "У тебе ще немає бізнесів"),
            ctx="business my places",
        )
        if _has_button(msg, "Назад"):
            msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains(text, ("Оберіть дію:",), ctx="business back to menu")
        else:
            assert_contains(text, ("Оберіть дію:",), ctx="business already on menu")

        msg = await click_button_and_wait(conv, msg, "💳 Плани", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Плани", "Немає підтверджених закладів"),
            ctx="business plans",
        )
        if _has_button(msg, "Меню"):
            msg = await click_button_and_wait(conv, msg, "Меню", ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains(text, ("Оберіть дію:",), ctx="business plans back")
        else:
            assert_contains(text, ("Оберіть дію:",), ctx="business plans already on menu")

        # Start add-flow and cancel immediately (idempotent flow smoke).
        msg = await click_button_and_wait(conv, msg, "Додати бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Оберіть категорію", "Немає жодної категорії"),
            ctx="business add menu",
        )
        if _has_button(msg, "Скасувати"):
            msg = await click_button_and_wait(conv, msg, "Скасувати", ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains(text, ("Оберіть дію:",), ctx="business add cancel")
        else:
            assert_contains(text, ("Оберіть дію:",), ctx="business add fallback menu")

        # Start attach-flow and cancel immediately (idempotent flow smoke).
        msg = await click_button_and_wait(conv, msg, "Прив'язати бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Введи код прив'язки",), ctx="business attach menu")
        msg = await click_button_and_wait(conv, msg, "Скасувати", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="business attach cancel")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("business scenario completed in %sms", elapsed)
    return ScenarioResult(name="business", status="ok", duration_ms=elapsed, message="passed")
