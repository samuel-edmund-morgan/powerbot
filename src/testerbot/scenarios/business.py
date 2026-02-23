"""Business bot smoke scenario."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from telethon.errors.rpcerrorlist import MessageIdInvalidError

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import extract_text, find_button

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    name: str
    status: str
    duration_ms: int
    message: str


def _has_button(message, needle: str) -> bool:
    try:
        find_button(message, needle)
        return True
    except AssertionError:
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


async def run(ctx) -> ScenarioResult:
    """Business path (stable): /start -> my places/plans -> add/attach cancel flows."""
    started = time.perf_counter()
    target = ctx.cfg.targets.businessbot
    bot_id = await ctx.client.get_peer_id(target)

    async def wait_bot_message(*, predicate, ctx_name: str):
        deadline = time.monotonic() + ctx.cfg.timeout_sec
        last_text = ""
        while time.monotonic() < deadline:
            msgs = await ctx.client.get_messages(target, limit=12)
            latest_msg = None
            latest_text = ""
            for msg in msgs:
                if getattr(msg, "out", False):
                    continue
                if getattr(msg, "sender_id", None) != bot_id:
                    continue
                text = extract_text(msg)
                if not text:
                    continue
                latest_msg = msg
                latest_text = text
                break
            if latest_msg is not None:
                last_text = latest_text
                if predicate(latest_msg, latest_text):
                    return latest_msg, latest_text
            await asyncio.sleep(0.6)
        raise AssertionError(f"{ctx_name}: timeout waiting bot message. last_text=\n{last_text}")

    async def latest_bot_message(ctx_name: str):
        msgs = await ctx.client.get_messages(target, limit=12)
        for msg in msgs:
            if getattr(msg, "out", False):
                continue
            if getattr(msg, "sender_id", None) != bot_id:
                continue
            return msg, extract_text(msg)
        raise AssertionError(f"{ctx_name}: no incoming bot message found")

    async def click_and_wait(message, needle: str, *, predicate, ctx_name: str):
        current = message
        for _ in range(4):
            try:
                i, j = find_button(current, needle)
            except AssertionError:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _has_button(m, needle),
                    ctx_name=f"{ctx_name} (refresh buttons)",
                )
                continue
            try:
                await current.click(i, j)
                try:
                    return await wait_bot_message(predicate=predicate, ctx_name=ctx_name)
                except AssertionError:
                    current, _ = await latest_bot_message(f"{ctx_name} (refresh after no update)")
                    continue
            except MessageIdInvalidError:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _has_button(m, needle),
                    ctx_name=f"{ctx_name} (refresh stale message)",
                )
                continue
        raise AssertionError(f"{ctx_name}: unable to click button `{needle}` on current bot message")

    async def click_first_non_nav_button(message, *, predicate, ctx_name: str):
        current = message
        for _ in range(4):
            buttons = getattr(current, "buttons", None) or []
            for row_idx, row in enumerate(buttons):
                for btn_idx, btn in enumerate(row):
                    label = str(getattr(btn, "text", "")).strip()
                    if _is_nav_button(label):
                        continue
                    try:
                        await current.click(row_idx, btn_idx)
                    except MessageIdInvalidError:
                        current, _ = await wait_bot_message(
                            predicate=lambda m, _t: bool(getattr(m, "buttons", None)),
                            ctx_name=f"{ctx_name} (refresh stale message)",
                        )
                        break
                    return await wait_bot_message(predicate=predicate, ctx_name=ctx_name)
                else:
                    continue
                break
            else:
                raise AssertionError(f"{ctx_name}: no non-navigation button found")
            # stale message path: retry with refreshed message
            continue
        raise AssertionError(f"{ctx_name}: unable to click non-navigation button on current bot message")

    async def ensure_main_menu(message):
        current = message
        current_text = extract_text(current)
        for _ in range(6):
            if "Оберіть дію:" in current_text:
                return current, current_text
            if _has_button(current, "Меню"):
                current, current_text = await click_and_wait(
                    current,
                    "Меню",
                    predicate=lambda _m, t: "Оберіть дію:" in t,
                    ctx_name="business ensure main menu via menu",
                )
                continue
            if _has_button(current, "Назад"):
                current, current_text = await click_and_wait(
                    current,
                    "Назад",
                    predicate=lambda _m, t: (
                        "Оберіть дію:" in t
                        or "Оберіть заклад" in t
                        or "Обери тариф для" in t
                        or "Плани" in t
                    ),
                    ctx_name="business ensure main menu via back",
                )
                continue
            break
        assert_contains(current_text, ("Оберіть дію:",), ctx="business ensure main menu")
        return current, current_text

    await ctx.client.send_message(ctx.cfg.targets.businessbot, "/start")
    msg, text = await wait_bot_message(
        predicate=lambda m, t: ("Бізнес-кабінет" in t) and _has_button(m, "Мої бізнеси"),
        ctx_name="business /start",
    )
    assert_contains(text, ("Бізнес-кабінет",), ctx="business /start")
    assert_contains(text, ("Оберіть дію:",), ctx="business /start action prompt")

    msg, text = await click_and_wait(
        msg,
        "🏢 Мої бізнеси",
        predicate=lambda _m, t: ("Оберіть заклад" in t) or ("У тебе ще немає бізнесів" in t),
        ctx_name="business my places",
    )
    assert_contains_any(
        text,
        ("Оберіть заклад", "У тебе ще немає бізнесів"),
        ctx="business my places",
    )

    if "Оберіть заклад" in text:
        msg, text = await click_first_non_nav_button(
            msg,
            predicate=lambda _m, t: ("Статус доступу" in t) or ("Тариф" in t) or ("Активно до" in t),
            ctx_name="business owner card open",
        )
        assert_contains_any(
            text,
            ("Статус доступу", "Тариф", "Активно до"),
            ctx="business owner card",
        )

        if _has_button(msg, "Змінити план"):
            msg, text = await click_and_wait(
                msg,
                "Змінити план",
                predicate=lambda _m, t: (
                    "Обери тариф для" in t
                    or "Оберіть заклад" in t
                    or "Немає підтверджених закладів" in t
                ),
                ctx_name="business owner card open plans",
            )
            assert_contains_any(
                text,
                ("Обери тариф для", "Оберіть заклад", "Немає підтверджених закладів"),
                ctx="business owner card open plans",
            )
            if "Обери тариф для" in text and _has_button(msg, "Назад"):
                msg, text = await click_and_wait(
                    msg,
                    "Назад",
                    predicate=lambda _m, t: ("Статус доступу" in t) or ("Тариф" in t) or ("Активно до" in t),
                    ctx_name="business owner plans back to card",
                )
                assert_contains_any(
                    text,
                    ("Статус доступу", "Тариф", "Активно до"),
                    ctx="business owner plans back to card",
                )

        if _has_button(msg, "Мої бізнеси"):
            msg, text = await click_and_wait(
                msg,
                "Мої бізнеси",
                predicate=lambda _m, t: ("Оберіть заклад" in t) or ("У тебе ще немає бізнесів" in t),
                ctx_name="business back to my places list",
            )
            assert_contains_any(
                text,
                ("Оберіть заклад", "У тебе ще немає бізнесів"),
                ctx="business back to my places list",
            )

    msg, _ = await ensure_main_menu(msg)

    msg, text = await click_and_wait(
        msg,
        "💳 Плани",
        predicate=lambda _m, t: ("Плани" in t) or ("Немає підтверджених закладів" in t),
        ctx_name="business plans",
    )
    assert_contains_any(
        text,
        ("Плани", "Немає підтверджених закладів"),
        ctx="business plans",
    )

    if "Оберіть заклад" in text:
        msg, text = await click_first_non_nav_button(
            msg,
            predicate=lambda _m, t: ("Обери тариф для" in t) or ("Плани" in t),
            ctx_name="business plans place menu open",
        )
        assert_contains_any(
            text,
            ("Обери тариф для", "Плани"),
            ctx="business plans place menu",
        )
        if _has_button(msg, "Назад"):
            msg, text = await click_and_wait(
                msg,
                "Назад",
                predicate=lambda _m, t: ("Оберіть заклад" in t) or ("Немає підтверджених закладів" in t),
                ctx_name="business plans place back",
            )
            assert_contains_any(
                text,
                ("Оберіть заклад", "Немає підтверджених закладів"),
                ctx="business plans place back",
            )

    msg, _ = await ensure_main_menu(msg)

    msg, text = await click_and_wait(
        msg,
        "Додати бізнес",
        predicate=lambda _m, t: ("Оберіть категорію" in t) or ("Немає жодної категорії" in t),
        ctx_name="business add menu",
    )
    assert_contains_any(
        text,
        ("Оберіть категорію", "Немає жодної категорії"),
        ctx="business add menu",
    )
    if _has_button(msg, "Скасувати"):
        msg, text = await click_and_wait(
            msg,
            "Скасувати",
            predicate=lambda _m, t: "Оберіть дію:" in t,
            ctx_name="business add cancel",
        )
        assert_contains(text, ("Оберіть дію:",), ctx="business add cancel")
    else:
        assert_contains(text, ("Оберіть дію:",), ctx="business add fallback menu")

    msg, text = await click_and_wait(
        msg,
        "Прив'язати бізнес",
        predicate=lambda _m, t: "Введи код прив'язки" in t,
        ctx_name="business attach menu",
    )
    assert_contains(text, ("Введи код прив'язки",), ctx="business attach menu")
    msg, text = await click_and_wait(
        msg,
        "Скасувати",
        predicate=lambda _m, t: "Оберіть дію:" in t,
        ctx_name="business attach cancel",
    )
    assert_contains(text, ("Оберіть дію:",), ctx="business attach cancel")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("business scenario completed in %sms", elapsed)
    return ScenarioResult(name="business", status="ok", duration_ms=elapsed, message="passed")
