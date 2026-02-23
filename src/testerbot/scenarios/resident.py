"""Resident bot smoke scenarios."""

from __future__ import annotations

import logging
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


def _is_nav_button(label: str) -> bool:
    normalized = label.casefold()
    return ("меню" in normalized) or ("назад" in normalized)


async def _click_first_non_nav_button(conv, message, timeout_sec: int):
    buttons = getattr(message, "buttons", None) or []
    for row_idx, row in enumerate(buttons):
        for btn_idx, btn in enumerate(row):
            label = str(getattr(btn, "text", "")).strip()
            if not label or _is_nav_button(label):
                continue
            await message.click(row_idx, btn_idx)
            return await wait_for_bot_response(conv, timeout_sec)
    raise AssertionError("resident places: no non-navigation button found")


async def run(ctx) -> ScenarioResult:
    """Resident path: /start -> building/section -> utilities/alerts/places/search."""
    started = time.perf_counter()
    async with ctx.client.conversation(ctx.cfg.targets.powerbot, timeout=ctx.cfg.timeout_sec) as conv:
        await conv.send_message("/start")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        # Resident bot can return a transient "Оновлюю меню…" before final menu render.
        if "Оновлюю меню" in text:
            for _ in range(4):
                msg = await wait_for_bot_response(conv, ctx.cfg.timeout_sec)
                text = extract_text(msg)
                if "Головне меню" in text:
                    break
        assert_contains(text, ("Головне меню",), ctx="resident /start")

        msg = await click_button_and_wait(conv, msg, "Обрати будинок", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Оберіть ваш будинок", "Оберіть свій будинок"),
            ctx="resident buildings",
        )

        msg = await click_button_and_wait(conv, msg, ctx.cfg.building_label, ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Оберіть секцію", "оберіть вашу секцію"),
            ctx="resident section menu",
        )

        msg = await click_button_and_wait(conv, msg, ctx.cfg.section_label, ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Секцію", "Секція"),
            ctx="resident section saved",
        )
        assert_contains_any(
            text,
            ("Збережено", "збережено", "Змінено", "змінено"),
            ctx="resident section saved status",
        )

        msg = await click_button_and_wait(conv, msg, "Світло/опалення/вода", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Світло",), ctx="resident utilities")

        msg = await click_button_and_wait(conv, msg, "Світло", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Стан електропостачання",), ctx="resident light status")

        # Back to main menu.
        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        msg = await click_button_and_wait(conv, msg, "Меню", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Головне меню",), ctx="resident back to main menu")

        # Alerts flow.
        msg = await click_button_and_wait(conv, msg, "Тривоги та укриття", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Тривоги та укриття",), ctx="resident alerts menu")

        msg = await click_button_and_wait(conv, msg, "Стан тривоги", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("ПОВІТРЯНА ТРИВОГА", "Відбій тривоги", "Статус невідомий"),
            ctx="resident alert status",
        )

        msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        msg = await click_button_and_wait(conv, msg, "Меню", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Головне меню",), ctx="resident alerts back to menu")

        # Places flow.
        msg = await click_button_and_wait(conv, msg, "Заклади в ЖК", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Заклади в ЖК",), ctx="resident places menu")
        assert_contains_any(text, ("Оберіть категорію", "Поки що категорій немає"), ctx="resident places categories")

        if "Поки що категорій немає" not in text:
            # Pick first category.
            msg = await _click_first_non_nav_button(conv, msg, ctx.cfg.timeout_sec)
            text = extract_text(msg)
            assert_contains_any(
                text,
                ("Оберіть заклад", "Закладів поки немає"),
                ctx="resident places category list",
            )

            # Open first place card (read-only) and go back to list.
            if "Закладів поки немає" not in text:
                msg = await _click_first_non_nav_button(conv, msg, ctx.cfg.timeout_sec)
                text = extract_text(msg)
                assert_contains_any(
                    text,
                    ("Лайків", "Адреса", "Категорія", "Заклад"),
                    ctx="resident place card",
                )
                msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
                text = extract_text(msg)
                assert_contains_any(
                    text,
                    ("Оберіть заклад", "Закладів поки немає"),
                    ctx="resident place card back to list",
                )

            msg = await click_button_and_wait(conv, msg, "Назад", ctx.cfg.timeout_sec)
        msg = await click_button_and_wait(conv, msg, "Меню", ctx.cfg.timeout_sec)

        # Search flow.
        msg = await click_button_and_wait(conv, msg, "Пошук закладу", ctx.cfg.timeout_sec)
        text = extract_text(msg)
        assert_contains(text, ("Пошук закладів",), ctx="resident search menu")

        await conv.send_message("сирники")
        msg = await ctx.wait_msg(conv)
        text = extract_text(msg)
        assert_contains_any(
            text,
            ("Результати пошуку", "нічого не знайдено", "нічого не знайдено."),
            ctx="resident search result",
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("resident scenario completed in %sms", elapsed)
    return ScenarioResult(name="resident_powerbot", status="ok", duration_ms=elapsed, message="passed")
