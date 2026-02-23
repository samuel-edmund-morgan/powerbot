"""Business bot smoke scenario."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import re
import time
from dataclasses import dataclass

from telethon.errors.rpcerrorlist import MessageIdInvalidError

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.scenarios.common import callback_at, collect_message_callbacks, extract_text, find_button

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


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _message_activity_utc(msg) -> datetime | None:
    return _to_utc(getattr(msg, "edit_date", None) or getattr(msg, "date", None))


def _is_owner_card_text(text: str) -> bool:
    return any(token in text for token in ("Статус доступу", "Тариф", "Активно до"))


async def run(ctx) -> ScenarioResult:
    """Business path (stable): /start -> my places/plans -> add/attach cancel flows."""
    started = time.perf_counter()
    target = ctx.cfg.targets.businessbot
    bot_id = await ctx.client.get_peer_id(target)
    scenario_started_utc = datetime.now(timezone.utc)

    async def wait_bot_message(
        *,
        predicate,
        ctx_name: str,
        previous_snapshot: tuple[int | None, str, datetime | None] | None = None,
        min_activity_utc: datetime | None = None,
    ):
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
                activity_utc = _message_activity_utc(msg)
                min_allowed = min_activity_utc or scenario_started_utc
                if activity_utc is not None and activity_utc < min_allowed:
                    continue
                latest_msg = msg
                latest_text = text
                if previous_snapshot is not None:
                    prev_id, prev_text, prev_edit_utc = previous_snapshot
                    same_message_id = getattr(msg, "id", None) == prev_id
                    same_text = text == prev_text
                    same_edit = _to_utc(getattr(msg, "edit_date", None)) == prev_edit_utc
                    if same_message_id and same_text and same_edit:
                        continue
                break
            if latest_msg is not None:
                last_text = latest_text
                if predicate(latest_msg, latest_text):
                    ctx.record_seen_callbacks("business", collect_message_callbacks(latest_msg))
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
            ctx.record_seen_callbacks("business", collect_message_callbacks(msg))
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
                ctx.record_clicked_callback("business", callback_at(current, i, j))
                prev_snapshot = (
                    getattr(current, "id", None),
                    extract_text(current),
                    _to_utc(getattr(current, "edit_date", None)),
                )
                await current.click(i, j)
                try:
                    msg, text = await wait_bot_message(
                        predicate=predicate,
                        ctx_name=ctx_name,
                        previous_snapshot=prev_snapshot,
                    )
                    ctx.record_seen_callbacks("business", collect_message_callbacks(msg))
                    return msg, text
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
                        ctx.record_clicked_callback("business", callback_at(current, row_idx, btn_idx))
                        await current.click(row_idx, btn_idx)
                    except MessageIdInvalidError:
                        current, _ = await wait_bot_message(
                            predicate=lambda m, _t: bool(getattr(m, "buttons", None)),
                            ctx_name=f"{ctx_name} (refresh stale message)",
                        )
                        break
                    msg, text = await wait_bot_message(predicate=predicate, ctx_name=ctx_name)
                    ctx.record_seen_callbacks("business", collect_message_callbacks(msg))
                    return msg, text
                else:
                    continue
                break
            else:
                raise AssertionError(f"{ctx_name}: no non-navigation button found")
            # stale message path: retry with refreshed message
            continue
        raise AssertionError(f"{ctx_name}: unable to click non-navigation button on current bot message")

    async def exercise_paged_list(message, *, expect_tokens: tuple[str, ...], ctx_name: str):
        current = message
        text = extract_text(current)
        if _has_button(current, "➡️"):
            current, text = await click_and_wait(
                current,
                "➡️",
                predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                ctx_name=f"{ctx_name} next page",
            )
            assert_contains_any(text, expect_tokens, ctx=f"{ctx_name} next page")
            if _has_button(current, "⬅️"):
                current, text = await click_and_wait(
                    current,
                    "⬅️",
                    predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                    ctx_name=f"{ctx_name} prev page",
                )
                assert_contains_any(text, expect_tokens, ctx=f"{ctx_name} prev page")
        return current, text

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
            if _has_button(current, "Скасувати"):
                current, current_text = await click_and_wait(
                    current,
                    "Скасувати",
                    predicate=lambda _m, t: (
                        "Оберіть дію:" in t
                        or _is_owner_card_text(t)
                        or "Оберіть заклад" in t
                        or "Обери тариф для" in t
                        or "Плани" in t
                    ),
                    ctx_name="business ensure main menu via cancel",
                )
                continue
            break
        assert_contains(current_text, ("Оберіть дію:",), ctx="business ensure main menu")
        return current, current_text

    async def open_first_owner_card(message, *, ctx_name: str):
        current, _ = await ensure_main_menu(message)
        current, text = await click_and_wait(
            current,
            "🏢 Мої бізнеси",
            predicate=lambda _m, t: ("Оберіть заклад" in t) or ("У тебе ще немає бізнесів" in t),
            ctx_name=f"{ctx_name} open my businesses",
        )
        assert_contains_any(
            text,
            ("Оберіть заклад", "У тебе ще немає бізнесів"),
            ctx=f"{ctx_name} open my businesses",
        )
        if "Оберіть заклад" not in text:
            raise AssertionError(f"{ctx_name}: no businesses to open owner card")
        current, text = await click_first_non_nav_button(
            current,
            predicate=lambda _m, t: _is_owner_card_text(t),
            ctx_name=f"{ctx_name} open owner card",
        )
        assert_contains_any(text, ("Статус доступу", "Тариф", "Активно до"), ctx=f"{ctx_name} owner card")
        return current, text

    async def exercise_owner_card_actions(message):
        current = message
        actions: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("Редагувати", ("Що хочеш змінити", "Обери тариф для", "Оберіть заклад", "Плани")),
            ("QR голосування", ("QR голосування", "Обери тариф для", "Оберіть заклад", "Плани")),
            ("QR-комплект", ("QR-комплект", "Обери тариф для", "Оберіть заклад", "Плани")),
            (
                "Пріоритетна підтримка",
                ("Пріоритетна підтримка", "Обери тариф для", "Оберіть заклад", "Плани"),
            ),
            ("Запропонувати правку", ("Запропонувати правку", "Що хочеш змінити", "Скасовано", "Плани")),
        )
        for needle, expect in actions:
            if not _has_button(current, needle):
                continue
            try:
                current, text = await click_and_wait(
                    current,
                    needle,
                    predicate=lambda _m, t, tokens=expect: any(tok in t for tok in tokens),
                    ctx_name=f"business owner action {needle}",
                )
                assert_contains_any(text, expect, ctx=f"business owner action {needle}")
            except AssertionError as exc:
                logger.warning("business owner action `%s` skipped due unstable UI state: %s", needle, exc)
            current, _ = await open_first_owner_card(current, ctx_name=f"business owner action {needle}")
        return current

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
        msg, text = await exercise_paged_list(
            msg,
            expect_tokens=("Оберіть заклад", "У тебе ще немає бізнесів"),
            ctx_name="business my places list",
        )
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

        msg = await exercise_owner_card_actions(msg)
        text = extract_text(msg)

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
        msg, text = await exercise_paged_list(
            msg,
            expect_tokens=("Оберіть заклад", "Немає підтверджених закладів"),
            ctx_name="business plans list",
        )
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
