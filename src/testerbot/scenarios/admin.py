"""Admin bot smoke scenario."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

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


async def _open_business_subsection(conv, message, section_button: str, expect_tokens: tuple[str, ...], timeout_sec: int):
    msg = await click_button_and_wait(conv, message, section_button, timeout_sec)
    text = extract_text(msg)
    assert_contains_any(text, expect_tokens, ctx=f"admin business {section_button}")
    if _has_button(msg, "Бізнес"):
        msg = await click_button_and_wait(conv, msg, "Бізнес", timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес",), ctx=f"admin back from {section_button}")
    return msg


async def run(ctx) -> ScenarioResult:
    """Open admin bot and exercise core read-only sections/callbacks."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.adminbot, timeout=ctx.cfg.timeout_sec) as conv:
        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        if text.strip() in {"…", "...", "Оновлюю меню…"}:
            for _ in range(4):
                msg = await ctx.wait_msg(conv)
                text = extract_text(msg)
                if (
                    "Оберіть дію" in text
                    or "Доступно лише адміністраторам" in text
                    or "Лише для адмінів" in text
                ):
                    break
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
        assert_contains(text, ("Оберіть дію",), ctx="admin /start")

        msg = await click_button_and_wait(conv, msg, "Підписники", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Підписники",), ctx="admin subscribers")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin subscribers back")

        msg = await click_button_and_wait(conv, msg, "Сенсори", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Сенсори",), ctx="admin sensors")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin sensors back")

        msg = await click_button_and_wait(conv, msg, "🧾 Черга задач", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Черга задач",), ctx="admin jobs")
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin jobs back")

        msg = await click_button_and_wait(conv, msg, "Бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес", "Оберіть дію"), ctx="admin business menu")

        # Read-only pass through core business admin subsections.
        msg = await _open_business_subsection(
            conv,
            msg,
            "Модерація",
            ("Модерація", "Черга модерації"),
            ctx.cfg.timeout_sec,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Правки закладів",
            ("Правки закладів", "Черга порожня"),
            ctx.cfg.timeout_sec,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Підтримка Partner",
            ("Підтримка Partner", "Черга порожня"),
            ctx.cfg.timeout_sec,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Коди прив'язки",
            ("Коди прив'язки", "Оберіть дію"),
            ctx.cfg.timeout_sec,
        )

        msg = await click_button_and_wait(conv, msg, "Підписки", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Підписки",), ctx="admin business subscriptions")

        msg = await click_button_and_wait(conv, msg, "Бізнес", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Бізнес",), ctx="admin subscriptions back to business")

        msg = await click_button_and_wait(conv, msg, "Головне меню", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin business back to menu")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("admin scenario completed in %sms", elapsed)
    return ScenarioResult(name="admin", status="ok", duration_ms=elapsed, message="passed")
