"""Resident bot smoke scenarios."""

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
    """Full resident path: /start -> building/section -> light status."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.powerbot, timeout=ctx.cfg.timeout_sec) as conv:
        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        assert_contains(text, ("Головне меню",), ctx="resident /start")

        msg = await click_button_and_wait(conv, msg, "Обрати будинок", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть ваш будинок",), ctx="resident buildings")

        msg = await click_button_and_wait(conv, msg, ctx.cfg.building_label, ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть секцію",), ctx="resident section menu")

        msg = await click_button_and_wait(conv, msg, ctx.cfg.section_label, ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Секцію", "збережено"), ctx="resident section saved")

        msg = await click_button_and_wait(conv, msg, "Світло/опалення/вода", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Світло",), ctx="resident utilities")

        msg = await click_button_and_wait(conv, msg, "Світло", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Стан електропостачання",), ctx="resident light status")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("resident scenario completed in %sms", elapsed)
    return ScenarioResult(name="resident_powerbot", status="ok", duration_ms=elapsed, message="passed")
