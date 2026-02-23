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


async def _open_business_subsection(
    conv,
    message,
    section_button: str,
    expect_tokens: tuple[str, ...],
    timeout_sec: int,
    settle_fn=None,
):
    msg = await click_button_and_wait(conv, message, section_button, timeout_sec)
    if settle_fn is not None:
        msg, text = await settle_fn(msg)
    else:
        text = extract_text(msg)
    assert_contains_any(text, expect_tokens, ctx=f"admin business {section_button}")
    if _has_button(msg, "Бізнес"):
        msg = await click_button_and_wait(conv, msg, "Бізнес", timeout_sec)
        if settle_fn is not None:
            msg, text = await settle_fn(msg)
        else:
            text = extract_text(msg)
        assert_contains(text, ("Бізнес",), ctx=f"admin back from {section_button}")
    return msg


async def run(ctx) -> ScenarioResult:
    """Open admin bot and exercise core read-only sections/callbacks."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.adminbot, timeout=ctx.cfg.timeout_sec) as conv:
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

        msg, text = await click_and_settle(msg, "Підписники")
        assert_contains(text, ("Підписники",), ctx="admin subscribers")
        msg, text = await click_and_settle(msg, "Назад")
        assert_contains(text, ("Оберіть дію",), ctx="admin subscribers back")

        msg, text = await click_and_settle(msg, "Сенсори")
        assert_contains(text, ("Сенсори",), ctx="admin sensors")
        msg, text = await click_and_settle(msg, "Назад")
        assert_contains(text, ("Оберіть дію",), ctx="admin sensors back")

        msg, text = await click_and_settle(msg, "🧾 Черга задач")
        assert_contains(text, ("Черга задач",), ctx="admin jobs")
        msg, text = await click_and_settle(msg, "Назад")
        assert_contains(text, ("Оберіть дію",), ctx="admin jobs back")

        msg, text = await click_and_settle(msg, "Бізнес")
        assert_contains(text, ("Бізнес", "Оберіть дію"), ctx="admin business menu")

        # Read-only pass through core business admin subsections.
        msg = await _open_business_subsection(
            conv,
            msg,
            "Модерація",
            ("Модерація", "Черга модерації"),
            ctx.cfg.timeout_sec,
            settle_fn=settle,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Правки закладів",
            ("Правки закладів", "Черга порожня"),
            ctx.cfg.timeout_sec,
            settle_fn=settle,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Підтримка Partner",
            ("Підтримка Partner", "Черга порожня"),
            ctx.cfg.timeout_sec,
            settle_fn=settle,
        )
        msg = await _open_business_subsection(
            conv,
            msg,
            "Коди прив'язки",
            ("Коди прив'язки", "Оберіть дію"),
            ctx.cfg.timeout_sec,
            settle_fn=settle,
        )

        msg, text = await click_and_settle(msg, "Підписки")
        assert_contains(text, ("Підписки",), ctx="admin business subscriptions")

        msg, text = await click_and_settle(msg, "Бізнес")
        assert_contains(text, ("Бізнес",), ctx="admin subscriptions back to business")

        msg, text = await click_and_settle(msg, "Головне меню")
        assert_contains(text, ("Оберіть дію",), ctx="admin business back to menu")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("admin scenario completed in %sms", elapsed)
    return ScenarioResult(name="admin", status="ok", duration_ms=elapsed, message="passed")
