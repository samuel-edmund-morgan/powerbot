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
    """Open admin bot and exercise a read-only surface."""
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

        msg = await click_button_and_wait(conv, msg, "🧾 Черга задач", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Черга задач",), ctx="admin jobs")

        msg = await click_button_and_wait(conv, msg, "« Меню", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію:",), ctx="admin back to menu")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("admin scenario completed in %sms", elapsed)
    return ScenarioResult(name="admin", status="ok", duration_ms=elapsed, message="passed")
