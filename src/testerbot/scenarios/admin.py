"""Admin bot smoke scenario."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

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
    """Open admin bot and exercise core read-only sections/callbacks."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.adminbot, timeout=ctx.cfg.timeout_sec) as conv:
        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        if "Доступно лише адміністраторам" in text or "Лише для адмінів" in text:
            logger.info("admin scenario skipped: user is not admin")
            elapsed = int((time.perf_counter() - started) * 1000)
            return ScenarioResult(
                name="admin_skip",
                status="ok",
                duration_ms=elapsed,
                message=text[:120] or "not_admin",
            )

        # Expect admin menu title and a few core sections.
        assert_contains(text, ("Оберіть дію:",), ctx="admin /start")

        msg = await click_button_and_wait(conv, msg, "Підписники", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Підписники",), ctx="admin subscribers")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="admin subscribers back")

        msg = await click_button_and_wait(conv, msg, "Сенсори", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Сенсори",), ctx="admin sensors")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="admin sensors back")

        msg = await click_button_and_wait(conv, msg, "🧾 Черга задач", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Черга задач",), ctx="admin jobs")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="admin jobs back")

        msg = await click_button_and_wait(conv, msg, "Бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес", "Оберіть дію"), ctx="admin business menu")

        msg = await click_button_and_wait(conv, msg, "Підписки", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Підписки",), ctx="admin business subscriptions")

        msg = await click_button_and_wait(conv, msg, "Бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес",), ctx="admin subscriptions back to business")

        msg = await click_button_and_wait(conv, msg, "Головне меню", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="admin business back to menu")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("admin scenario completed in %sms", elapsed)
    return ScenarioResult(name="admin", status="ok", duration_ms=elapsed, message="passed")
