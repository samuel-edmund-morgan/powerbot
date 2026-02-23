"""Admin bot smoke scenario (polling-based, no Telethon Conversation races)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
import time

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
    if "назад" in normalized or "меню" in normalized or "бізнес" in normalized:
        return True
    if normalized in {"⬅️", "➡️"}:
        return True
    if "/" in normalized and all(part.strip().isdigit() for part in normalized.split("/", 1)):
        return True
    if normalized.startswith("«"):
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


async def run(ctx) -> ScenarioResult:
    """Open admin bot and exercise core read-only sections/callbacks."""
    started = time.perf_counter()
    target = ctx.cfg.targets.adminbot
    bot_id = await ctx.client.get_peer_id(target)
    scenario_started_utc = datetime.now(timezone.utc)

    async def latest_bot_message(ctx_name: str):
        msgs = await ctx.client.get_messages(target, limit=12)
        for msg in msgs:
            if getattr(msg, "out", False):
                continue
            if getattr(msg, "sender_id", None) != bot_id:
                continue
            text = extract_text(msg)
            if text:
                ctx.record_seen_callbacks("admin", collect_message_callbacks(msg))
                return msg, text
        raise AssertionError(f"{ctx_name}: no incoming admin-bot message found")

    async def wait_bot_message(
        *,
        predicate,
        ctx_name: str,
        previous_snapshot: tuple[int | None, str, datetime | None] | None = None,
        timeout_sec: int | None = None,
        min_activity_utc: datetime | None = None,
    ):
        deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else ctx.cfg.timeout_sec)
        last_text = ""
        while time.monotonic() < deadline:
            msgs = await ctx.client.get_messages(target, limit=12)
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
                last_text = text
                if previous_snapshot is not None:
                    prev_id, prev_text, prev_edit_utc = previous_snapshot
                    same_message_id = getattr(msg, "id", None) == prev_id
                    same_text = text == prev_text
                    same_edit = _to_utc(getattr(msg, "edit_date", None)) == prev_edit_utc
                    if same_message_id and same_text and same_edit:
                        continue
                if predicate(msg, text):
                    ctx.record_seen_callbacks("admin", collect_message_callbacks(msg))
                    return msg, text
            await asyncio.sleep(1.1)
        raise AssertionError(f"{ctx_name}: timeout waiting bot message. last_text=\n{last_text}")

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

            prev_snapshot = (
                getattr(current, "id", None),
                extract_text(current),
                _to_utc(getattr(current, "edit_date", None)),
            )
            try:
                ctx.record_clicked_callback("admin", callback_at(current, i, j))
                await current.click(i, j)
            except MessageIdInvalidError:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _has_button(m, needle),
                    ctx_name=f"{ctx_name} (refresh stale message)",
                )
                continue

            try:
                return await wait_bot_message(
                    predicate=predicate,
                    ctx_name=ctx_name,
                    previous_snapshot=prev_snapshot,
                )
            except AssertionError:
                current, _ = await latest_bot_message(f"{ctx_name} (refresh latest)")
                continue

        raise AssertionError(f"{ctx_name}: unable to click `{needle}`")

    async def click_first_non_nav_button(message, *, predicate, ctx_name: str):
        current = message
        for _ in range(4):
            buttons = getattr(current, "buttons", None) or []
            for row_idx, row in enumerate(buttons):
                for btn_idx, btn in enumerate(row):
                    label = str(getattr(btn, "text", "")).strip()
                    if _is_nav_button(label):
                        continue
                    prev_snapshot = (
                        getattr(current, "id", None),
                        extract_text(current),
                        _to_utc(getattr(current, "edit_date", None)),
                    )
                    try:
                        ctx.record_clicked_callback("admin", callback_at(current, row_idx, btn_idx))
                        await current.click(row_idx, btn_idx)
                    except MessageIdInvalidError:
                        current, _ = await wait_bot_message(
                            predicate=lambda m, _t: bool(getattr(m, "buttons", None)),
                            ctx_name=f"{ctx_name} (refresh stale message)",
                        )
                        break
                    try:
                        msg, text = await wait_bot_message(
                            predicate=predicate,
                            ctx_name=ctx_name,
                            previous_snapshot=prev_snapshot,
                        )
                        ctx.record_seen_callbacks("admin", collect_message_callbacks(msg))
                        return msg, text
                    except AssertionError:
                        current, _ = await latest_bot_message(f"{ctx_name} (refresh latest)")
                        break
                else:
                    continue
                break
            else:
                raise AssertionError(f"{ctx_name}: no non-navigation buttons to click")
        raise AssertionError(f"{ctx_name}: unable to click non-navigation button")

    async def ensure_main_menu(message):
        current = message
        for _ in range(6):
            current_text = extract_text(current)
            if "Оберіть дію" in current_text:
                return current, current_text
            if _has_button(current, "Головне меню"):
                current, current_text = await click_and_wait(
                    current,
                    "Головне меню",
                    predicate=lambda _m, t: "Оберіть дію" in t,
                    ctx_name="admin ensure main via home",
                )
                continue
            if _has_button(current, "Назад"):
                current, current_text = await click_and_wait(
                    current,
                    "Назад",
                    predicate=lambda _m, t: ("Оберіть дію" in t) or ("Бізнес" in t),
                    ctx_name="admin ensure main via back",
                )
                continue
            current, current_text = await latest_bot_message("admin ensure main latest")
            if "Оберіть дію" in current_text:
                return current, current_text
            break
        raise AssertionError("admin ensure main menu: unable to return to main menu without extra /start")

    async def ensure_business_menu(message):
        current = message
        for _ in range(8):
            current_text = extract_text(current)
            if "Бізнес" in current_text and (
                _has_button(current, "Модерація") or _has_button(current, "Коди прив'язки")
            ):
                return current, current_text
            if _has_button(current, "Бізнес"):
                current, current_text = await click_and_wait(
                    current,
                    "Бізнес",
                    predicate=lambda m, t: (
                        (
                            "Бізнес" in t
                            and (_has_button(m, "Модерація") or _has_button(m, "Коди прив'язки"))
                        )
                        or ("Оберіть дію" in t and _has_button(m, "Сенсори") and _has_button(m, "Бізнес"))
                    ),
                    ctx_name="admin ensure business via business",
                )
                continue
            if _has_button(current, "Головне меню"):
                current, _ = await click_and_wait(
                    current,
                    "Головне меню",
                    predicate=lambda _m, t: "Оберіть дію" in t,
                    ctx_name="admin ensure business via home",
                )
                continue
            if _has_button(current, "Назад"):
                current, current_text = await click_and_wait(
                    current,
                    "Назад",
                    predicate=lambda m, t: ("Бізнес" in t) or ("Оберіть дію" in t) or _has_button(m, "Бізнес"),
                    ctx_name="admin ensure business via back",
                )
                continue
            current, current_text = await latest_bot_message("admin ensure business latest")
            if "Бізнес" in current_text and (
                _has_button(current, "Модерація") or _has_button(current, "Коди прив'язки")
            ):
                return current, current_text
        raise AssertionError("admin ensure business menu: unable to open Бізнес")

    sent_start = await ctx.client.send_message(target, "/start")
    sent_start_utc = _to_utc(getattr(sent_start, "date", None)) or scenario_started_utc
    try:
        msg, text = await wait_bot_message(
            predicate=lambda m, t: ("Оберіть дію" in t) or ("Доступно лише адміністраторам" in t) or ("Лише для адмінів" in t),
            ctx_name="admin /start",
            timeout_sec=max(ctx.cfg.timeout_sec * 2, 45),
            min_activity_utc=sent_start_utc,
        )
    except AssertionError:
        # Fallback to latest bot message in case of transient history lag/flood waits.
        msg, text = await latest_bot_message("admin /start fallback latest")
        if ("Оберіть дію" not in text) and ("Доступно лише адміністраторам" not in text) and ("Лише для адмінів" not in text):
            raise
    if "Доступно лише адміністраторам" in text or "Лише для адмінів" in text:
        logger.info("admin scenario skipped: user is not admin")
        elapsed = int((time.perf_counter() - started) * 1000)
        return ScenarioResult(
            name="admin_skip",
            status="ok",
            duration_ms=elapsed,
            message=text[:120] or "not_admin",
        )

    assert_contains(text, ("Оберіть дію",), ctx="admin /start")
    msg, text = await ensure_main_menu(msg)

    def require_button(current_msg, needle: str, ctx_name: str) -> None:
        if not _has_button(current_msg, needle):
            raise AssertionError(f"{ctx_name}: expected button containing `{needle}`")

    # Core admin sections.
    for section, expect in (
        ("Підписники", ("Підписники",)),
        ("Сенсори", ("Сенсори",)),
        ("Черга задач", ("Черга задач",)),
    ):
        require_button(msg, section, "admin main menu")
        msg, text = await click_and_wait(
            msg,
            section,
            predicate=lambda _m, t, tokens=expect: any(tok in t for tok in tokens),
            ctx_name=f"admin {section}",
        )
        assert_contains_any(text, expect, ctx=f"admin {section}")
        msg, text = await ensure_main_menu(msg)
        assert_contains(text, ("Оберіть дію",), ctx=f"admin {section} back")

    # Open broadcast composer and cancel (covers admin_cancel read-only callback).
    require_button(msg, "Розсилка", "admin main menu")
    msg, text = await click_and_wait(
        msg,
        "Розсилка",
        predicate=lambda _m, t: ("Введіть текст" in t) or ("Надішліть текст" in t) or ("розсилк" in t.casefold()),
        ctx_name="admin broadcast open",
    )
    assert_contains_any(text, ("Введіть текст", "Надішліть текст", "розсилк"), ctx="admin broadcast open")
    require_button(msg, "Скасувати", "admin broadcast composer")
    msg, text = await click_and_wait(
        msg,
        "Скасувати",
        predicate=lambda _m, t: "Оберіть дію" in t,
        ctx_name="admin broadcast cancel",
    )
    assert_contains(text, ("Оберіть дію",), ctx="admin broadcast cancel")

    # Business section and read-only subsections.
    if _has_button(msg, "Бізнес"):
        msg, text = await ensure_business_menu(msg)
        assert_contains(text, ("Бізнес",), ctx="admin business menu")

        for button, expect_tokens in (
            ("Модерація", ("Модерація", "Черга модерації")),
            ("Правки закладів", ("Правки закладів", "Черга порожня")),
            ("Підтримка Partner", ("Підтримка Partner", "Черга порожня")),
            ("Підписки", ("Підписки",)),
        ):
            msg, _ = await ensure_business_menu(msg)
            if not _has_button(msg, button):
                continue
            msg, text = await click_and_wait(
                msg,
                button,
                predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                ctx_name=f"admin business {button}",
            )
            assert_contains_any(text, expect_tokens, ctx=f"admin business {button}")

        # Claim tokens read-only deep flow:
        # menu -> list places -> first service -> places list -> back categories -> back tokens menu
        msg, _ = await ensure_business_menu(msg)
        if _has_button(msg, "Коди прив'язки"):
            msg, text = await click_and_wait(
                msg,
                "Коди прив'язки",
                predicate=lambda _m, t: "Коди прив'язки" in t,
                ctx_name="admin business tokens menu open",
            )
            if _has_button(msg, "Список закладів"):
                msg, text = await click_and_wait(
                    msg,
                    "Список закладів",
                    predicate=lambda _m, t: ("Список закладів" in t) and ("категор" in t.casefold()),
                    ctx_name="admin business tokens services open",
                )
                if _has_button(msg, "Категорії") or _has_button(msg, "Коди прив'язки") or _has_button(msg, "Бізнес"):
                    msg, text = await click_first_non_nav_button(
                        msg,
                        predicate=lambda _m, t: "Список закладів" in t and ("заклад" in t.casefold()),
                        ctx_name="admin business tokens service pick",
                    )
                    if _has_button(msg, "Категорії"):
                        msg, text = await click_and_wait(
                            msg,
                            "Категорії",
                            predicate=lambda _m, t: ("Список закладів" in t) and ("категор" in t.casefold()),
                            ctx_name="admin business tokens places back to services",
                        )
                if _has_button(msg, "Коди прив'язки"):
                    msg, text = await click_and_wait(
                        msg,
                        "Коди прив'язки",
                        predicate=lambda _m, t: "Коди прив'язки" in t,
                        ctx_name="admin business tokens back to menu",
                    )

        msg, text = await ensure_main_menu(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin business back to main")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("admin scenario completed in %sms", elapsed)
    return ScenarioResult(name="admin", status="ok", duration_ms=elapsed, message="passed")
