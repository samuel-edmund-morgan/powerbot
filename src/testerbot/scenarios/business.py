"""Business bot smoke scenario."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from testerbot.assertions import assert_contains
from testerbot.scenarios.common import click_button_and_wait, extract_text

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    name: str
    status: str
    duration_ms: int
    message: str


async def run(ctx) -> ScenarioResult:
    """Open business bot and verify key menu screens."""
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
        assert_contains(text, ("Оберіть заклад",), ctx="business my places")

        msg = await click_button_and_wait(conv, msg, "« Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="business back to menu")

        msg = await click_button_and_wait(conv, msg, "💳 Плани", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Плани",), ctx="business plans")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("business scenario completed in %sms", elapsed)
    return ScenarioResult(name="business", status="ok", duration_ms=elapsed, message="passed")
