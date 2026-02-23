"""Resident bot smoke scenarios."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import callback_at, collect_message_callbacks, extract_text, find_button

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


def _has_button(message, needle: str) -> bool:
    try:
        find_button(message, needle)
        return True
    except AssertionError:
        return False


def _text_has_any(text: str, *needles: str) -> bool:
    hay = (text or "").casefold()
    return any((needle or "").casefold() in hay for needle in needles)


async def run(ctx) -> ScenarioResult:
    """Resident path (stable): /start -> building menu -> alerts -> places -> search."""
    started = time.perf_counter()
    target = ctx.cfg.targets.powerbot
    bot_id = await ctx.client.get_peer_id(target)

    async def wait_bot_message(*, predicate, ctx_name: str):
        deadline = time.monotonic() + ctx.cfg.timeout_sec
        last_text = ""
        while time.monotonic() < deadline:
            msgs = await ctx.client.get_messages(target, limit=10)
            for msg in msgs:
                if getattr(msg, "out", False):
                    continue
                if getattr(msg, "sender_id", None) != bot_id:
                    continue
                text = extract_text(msg)
                if not text:
                    continue
                last_text = text
                if predicate(msg, text):
                    ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
                    return msg, text
            await asyncio.sleep(0.6)
        raise AssertionError(f"{ctx_name}: timeout waiting bot message. last_text=\n{last_text}")

    async def click_and_wait(message, needle: str, *, predicate, ctx_name: str):
        i, j = find_button(message, needle)
        ctx.record_clicked_callback("resident", callback_at(message, i, j))
        await message.click(i, j)
        msg, text = await wait_bot_message(predicate=predicate, ctx_name=ctx_name)
        ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
        return msg, text

    async def click_first_non_nav_button(message, *, predicate, ctx_name: str):
        buttons = getattr(message, "buttons", None) or []
        for row_idx, row in enumerate(buttons):
            for btn_idx, btn in enumerate(row):
                label = str(getattr(btn, "text", "")).strip()
                if _is_nav_button(label):
                    continue
                ctx.record_clicked_callback("resident", callback_at(message, row_idx, btn_idx))
                await message.click(row_idx, btn_idx)
                msg, text = await wait_bot_message(predicate=predicate, ctx_name=ctx_name)
                ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
                return msg, text
        raise AssertionError(f"{ctx_name}: no non-navigation buttons to click")

    await ctx.client.send_message(target, "/start")
    msg, text = await wait_bot_message(
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Обрати будинок"),
        ctx_name="resident /start",
    )
    assert_contains(text, ("Головне меню",), ctx="resident /start")

    msg, text = await click_and_wait(
        msg,
        "Обрати будинок",
        predicate=lambda _m, t: ("Оберіть свій будинок" in t) or ("Оберіть ваш будинок" in t),
        ctx_name="resident buildings",
    )
    assert_contains_any(
        text,
        ("Оберіть ваш будинок", "Оберіть свій будинок"),
        ctx="resident buildings",
    )

    # Return to main menu without mutating building/section selection.
    msg, text = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
        ctx_name="resident back to menu",
    )
    assert_contains(text, ("Головне меню",), ctx="resident back to menu")

    # Utilities flow.
    msg, text = await click_and_wait(
        msg,
        "Світло/опалення/вода",
        predicate=lambda m, t: _text_has_any(t, "оберіть розділ", "що перевірити") or _has_button(m, "Статистика"),
        ctx_name="resident utilities menu",
    )
    assert_contains_any(
        text,
        ("Оберіть, що перевірити", "Оберіть що перевірити", "Світло", "Статистика"),
        ctx="resident utilities menu",
    )

    msg, text = await click_and_wait(
        msg,
        "Світло",
        predicate=lambda _m, t: _text_has_any(t, "стан електропостачання", "світла", "світло є"),
        ctx_name="resident utilities status",
    )
    assert_contains_any(text, ("Стан електропостачання", "Світла"), ctx="resident utilities status")

    msg, text = await click_and_wait(
        msg,
        "Назад",
        predicate=lambda m, t: _text_has_any(t, "оберіть розділ", "що перевірити") or _has_button(m, "Статистика"),
        ctx_name="resident utilities back from status",
    )
    assert_contains_any(text, ("Оберіть", "Світло", "Статистика"), ctx="resident utilities back from status")

    msg, text = await click_and_wait(
        msg,
        "Опалення",
        predicate=lambda _m, t: _text_has_any(t, "стан опалення", "опалення"),
        ctx_name="resident utilities heating",
    )
    assert_contains_any(text, ("Стан опалення", "Опалення"), ctx="resident utilities heating")
    msg, text = await click_and_wait(
        msg,
        "Назад",
        predicate=lambda m, t: _text_has_any(t, "оберіть розділ", "що перевірити") or _has_button(m, "Статистика"),
        ctx_name="resident utilities back from heating",
    )

    msg, text = await click_and_wait(
        msg,
        "Вода",
        predicate=lambda _m, t: _text_has_any(t, "стан води", "стан водопостачання", "вода", "води"),
        ctx_name="resident utilities water",
    )
    assert_contains_any(text, ("Стан водопостачання", "Стан води", "вода"), ctx="resident utilities water")
    msg, text = await click_and_wait(
        msg,
        "Назад",
        predicate=lambda m, t: _text_has_any(t, "оберіть розділ", "що перевірити") or _has_button(m, "Статистика"),
        ctx_name="resident utilities back from water",
    )

    msg, text = await click_and_wait(
        msg,
        "Статистика",
        predicate=lambda _m, t: ("Статистика" in t) and ("електропостачання" in t.casefold()),
        ctx_name="resident utilities stats",
    )
    assert_contains(text, ("Статистика",), ctx="resident utilities stats")

    for label in ("День", "Тиждень", "Місяць"):
        if not _has_button(msg, label):
            continue
        msg, text = await click_and_wait(
            msg,
            label,
            predicate=lambda _m, t: ("Статистика" in t) and ("електропостачання" in t.casefold()),
            ctx_name=f"resident utilities stats {label}",
        )
        assert_contains(text, ("Статистика",), ctx=f"resident utilities stats {label}")

    msg, text = await click_and_wait(
        msg,
        "Назад",
        predicate=lambda m, t: ("що перевірити" in t.casefold()) or _has_button(m, "Меню"),
        ctx_name="resident utilities back from stats",
    )
    msg, text = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Тривоги та укриття"),
        ctx_name="resident utilities back to menu",
    )

    # Service menu flow.
    msg, text = await click_and_wait(
        msg,
        "Сервісна служба",
        predicate=lambda _m, t: "Сервісна служба" in t,
        ctx_name="resident service menu",
    )
    assert_contains(text, ("Сервісна служба",), ctx="resident service menu")
    service_buttons = (
        "Адміністрація",
        "Бухгалтерія",
        "Охорона",
        "Сантехнік",
        "Електрик",
        "ІТ відділ",
        "Диспетчер ліфтів",
        "перепустки авто",
        "Оренда паркінгу",
    )
    for label in service_buttons:
        if not _has_button(msg, label):
            continue
        msg, text = await click_and_wait(
            msg,
            label,
            predicate=lambda m, _t: _has_button(m, "Назад"),
            ctx_name=f"resident service {label}",
        )
        msg, text = await click_and_wait(
            msg,
            "Назад",
            predicate=lambda _m, t: "Сервісна служба" in t,
            ctx_name=f"resident service {label} back",
        )
    msg, text = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Тривоги та укриття"),
        ctx_name="resident service back to menu",
    )

    # Notifications flow (read-only nav and quiet-hours screens).
    msg, text = await click_and_wait(
        msg,
        "Сповіщення",
        predicate=lambda _m, t: "Сповіщення" in t,
        ctx_name="resident notifications menu",
    )
    assert_contains(text, ("Сповіщення",), ctx="resident notifications menu")
    if _has_button(msg, "Тихі години"):
        msg, text = await click_and_wait(
            msg,
            "Тихі години",
            predicate=lambda _m, t: ("Тихі години" in t) or ("Не турбувати" in t),
            ctx_name="resident notifications quiet menu",
        )
        assert_contains_any(
            text,
            ("Тихі години", "Не турбувати"),
            ctx="resident notifications quiet menu",
        )
        if _has_button(msg, "ℹ️ Довідка") or _has_button(msg, "Довідка"):
            hint = "ℹ️ Довідка" if _has_button(msg, "ℹ️ Довідка") else "Довідка"
            msg, text = await click_and_wait(
                msg,
                hint,
                predicate=lambda _m, t: ("Тихі години" in t) or ("сповіщення" in t.casefold()),
                ctx_name="resident notifications quiet info",
            )
        msg, text = await click_and_wait(
            msg,
            "Назад",
            predicate=lambda _m, t: "Сповіщення" in t,
            ctx_name="resident notifications quiet back",
        )
    msg, text = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Тривоги та укриття"),
        ctx_name="resident notifications back to menu",
    )

    # Alerts flow.
    msg, text = await click_and_wait(
        msg,
        "Тривоги та укриття",
        predicate=lambda _m, t: "Тривоги та укриття" in t,
        ctx_name="resident alerts menu",
    )
    assert_contains(text, ("Тривоги та укриття",), ctx="resident alerts menu")

    msg, text = await click_and_wait(
        msg,
        "Стан тривоги",
        predicate=lambda _m, t: (
            ("ПОВІТРЯНА ТРИВОГА" in t) or ("Відбій тривоги" in t) or ("Статус невідомий" in t)
        ),
        ctx_name="resident alert status",
    )
    assert_contains_any(
        text,
        ("ПОВІТРЯНА ТРИВОГА", "Відбій тривоги", "Статус невідомий"),
        ctx="resident alert status",
    )

    if _has_button(msg, "Назад"):
        msg, text = await click_and_wait(
            msg,
            "Назад",
            predicate=lambda _m, t: "Тривоги та укриття" in t,
            ctx_name="resident alert status back",
        )
    if _has_button(msg, "Укриття"):
        msg, text = await click_and_wait(
            msg,
            "Укриття",
            predicate=lambda _m, t: ("Оберіть укриття" in t) or ("Укриття" in t),
            ctx_name="resident shelters list",
        )
        assert_contains_any(text, ("Укриття", "Оберіть укриття"), ctx="resident shelters list")
        if _has_button(msg, "Назад"):
            # Open one shelter card if available (non-nav button), then return.
            try:
                msg, _ = await click_first_non_nav_button(
                    msg,
                    predicate=lambda m, t: ("Адреса" in t) and _has_button(m, "Назад"),
                    ctx_name="resident shelters detail",
                )
                msg, _ = await click_and_wait(
                    msg,
                    "Назад",
                    predicate=lambda _m, t: ("Укриття" in t) or ("Оберіть укриття" in t),
                    ctx_name="resident shelters detail back",
                )
            except AssertionError:
                pass

    try:
        msg, _ = await click_and_wait(
            msg,
            "Меню",
            predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Заклади в ЖК"),
            ctx_name="resident alerts back to menu",
        )
    except AssertionError:
        msg, _ = await click_and_wait(
            msg,
            "Назад",
            predicate=lambda m, t: ("Тривоги та укриття" in t) and (_has_button(m, "Меню") or _has_button(m, "Назад")),
            ctx_name="resident alerts back step",
        )
        msg, _ = await click_and_wait(
            msg,
            "Меню",
            predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Заклади в ЖК"),
            ctx_name="resident alerts back to menu",
        )

    # Places flow (read-only).
    msg, text = await click_and_wait(
        msg,
        "Заклади в ЖК",
        predicate=lambda _m, t: "Заклади в ЖК" in t,
        ctx_name="resident places menu",
    )
    assert_contains(text, ("Заклади в ЖК",), ctx="resident places menu")
    assert_contains_any(text, ("Оберіть категорію", "Поки що категорій немає"), ctx="resident places categories")

    msg, _ = await click_and_wait(
        msg,
        "Меню",
        predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
        ctx_name="resident places back to menu",
    )

    # Search flow.
    msg, text = await click_and_wait(
        msg,
        "Пошук закладу",
        predicate=lambda _m, t: "Пошук закладів" in t,
        ctx_name="resident search menu",
    )
    assert_contains(text, ("Пошук закладів",), ctx="resident search menu")

    await ctx.client.send_message(target, "сирники")
    _, text = await wait_bot_message(
        predicate=lambda _m, t: (
            ("Результати пошуку" in t) or ("нічого не знайдено" in t) or ("нічого не знайдено." in t)
        ),
        ctx_name="resident search result",
    )
    assert_contains_any(
        text,
        ("Результати пошуку", "нічого не знайдено", "нічого не знайдено."),
        ctx="resident search result",
    )

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("resident scenario completed in %sms", elapsed)
    return ScenarioResult(name="resident_powerbot", status="ok", duration_ms=elapsed, message="passed")
