"""Business bot smoke scenario."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import click_button_and_wait, extract_text, wait_for_bot_response

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


def _is_nav_button(label: str) -> bool:
    normalized = str(label or "").strip().casefold()
    if not normalized:
        return True
    if "меню" in normalized or "назад" in normalized:
        return True
    if normalized in {"⬅️", "➡️"}:
        return True
    if re.fullmatch(r"\d+/\d+", normalized):
        return True
    return False


async def _click_first_non_nav_button(conv, message, timeout_sec: int):
    buttons = getattr(message, "buttons", None) or []
    for row_idx, row in enumerate(buttons):
        for btn_idx, btn in enumerate(row):
            label = str(getattr(btn, "text", "")).strip()
            if _is_nav_button(label):
                continue
            await message.click(row_idx, btn_idx)
            return await wait_for_bot_response(conv, timeout_sec)
    raise AssertionError("business scenario: no non-navigation button found")


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

        if "Оберіть заклад" in text:
            # Open first owner card and return back without mutating actions.
            msg = await _click_first_non_nav_button(conv, msg, ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains_any(
                text,
                ("Статус доступу", "Тариф", "Активно до"),
                ctx="business owner card",
            )

            # Open plans from owner card and return back to card.
            if _has_button(msg, "Змінити план"):
                msg = await click_button_and_wait(conv, msg, "Змінити план", ctx.cfg.timeout_sec)
                text = extract_text(msg)
                assert_contains_any(
                    text,
                    ("Обери тариф для", "Оберіть заклад", "Немає підтверджених закладів"),
                    ctx="business owner card open plans",
                )
                if "Обери тариф для" in text and _has_button(msg, "Назад"):
                    msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
                    text = extract_text(msg)
                    assert_contains_any(
                        text,
                        ("Статус доступу", "Тариф"),
                        ctx="business owner plans back to card",
                    )

            if _has_button(msg, "Мої бізнеси"):
                msg = await click_button_and_wait(conv, msg, "Мої бізнеси", ctx.cfg.timeout_sec)
                text = extract_text(msg)
                assert_contains_any(
                    text,
                    ("Оберіть заклад", "У тебе ще немає бізнесів"),
                    ctx="business back to my places list",
                )

        msg = await _ensure_main_menu(conv, msg, ctx.cfg.timeout_sec)

        msg = await click_button_and_wait(conv, msg, "💳 Плани", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Плани", "Немає підтверджених закладів"),
            ctx="business plans",
        )

        if "Оберіть заклад" in text:
            msg = await _click_first_non_nav_button(conv, msg, ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains_any(
                text,
                ("Обери тариф для", "Плани"),
                ctx="business plans place menu",
            )
            if _has_button(msg, "Назад"):
                msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
                text = extract_text(msg)
                assert_contains_any(
                    text,
                    ("Оберіть заклад", "Немає підтверджених закладів"),
                    ctx="business plans place back",
                )

        msg = await _ensure_main_menu(conv, msg, ctx.cfg.timeout_sec)

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
