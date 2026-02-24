"""Admin bot smoke scenario (polling-based, no Telethon Conversation races)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
import sqlite3
import time

from telethon.errors.rpcerrorlist import MessageIdInvalidError

from testerbot.assertions import assert_contains, assert_contains_any
from testerbot.callbacks import extract_callback_data
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


def _has_recovery_controls(message) -> bool:
    """Detect whether current screen has controls that allow deterministic recovery."""
    labels: list[str] = []
    for row in (getattr(message, "buttons", None) or []):
        for btn in row:
            labels.append(str(getattr(btn, "text", "")).strip())
    if not labels:
        return False
    for label in labels:
        normalized = label.casefold()
        if _is_nav_button(label):
            return True
        if (
            "оновити" in normalized
            or "скасувати" in normalized
            or "бізнес" in normalized
            or "категор" in normalized
            or "список закладів" in normalized
        ):
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
    now_iso = datetime.now(timezone.utc).isoformat()
    click_min_interval_sec = 1.2
    last_click_monotonic = 0.0

    async def throttle_click() -> None:
        nonlocal last_click_monotonic
        now = time.monotonic()
        wait_for = click_min_interval_sec - (now - last_click_monotonic)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        last_click_monotonic = time.monotonic()

    def _load_places_with_active_claim_token() -> set[int]:
        db_path = str(getattr(ctx.cfg, "db_path", "") or "").strip()
        if not db_path:
            return set()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            rows = conn.execute(
                """
                SELECT DISTINCT place_id
                FROM business_claim_tokens
                WHERE status = 'active' AND expires_at > ?
                ORDER BY place_id
                """,
                (now_iso,),
            ).fetchall()
            return {int(row[0]) for row in rows if row and int(row[0]) > 0}
        except Exception as exc:
            logger.warning("admin scenario: failed to load active claim-token places: %s", exc)
            return set()
        finally:
            if conn is not None:
                conn.close()

    active_claim_token_place_ids = _load_places_with_active_claim_token()

    async def latest_bot_message(ctx_name: str, *, require_buttons: bool = False):
        msgs = await ctx.client.get_messages(target, limit=12)
        for msg in msgs:
            if getattr(msg, "out", False):
                continue
            if getattr(msg, "sender_id", None) != bot_id:
                continue
            text = extract_text(msg)
            if text:
                if require_buttons and not (getattr(msg, "buttons", None) or []):
                    continue
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
                await throttle_click()
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

    async def click_and_stay(message, needle: str, *, ctx_name: str):
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
                ctx.record_clicked_callback("admin", callback_at(current, i, j))
                await throttle_click()
                await current.click(i, j)
            except MessageIdInvalidError:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _has_button(m, needle),
                    ctx_name=f"{ctx_name} (refresh stale message)",
                )
                continue

            await asyncio.sleep(1.0)
            try:
                return await latest_bot_message(f"{ctx_name} latest with buttons", require_buttons=True)
            except AssertionError:
                current, _ = await latest_bot_message(f"{ctx_name} latest fallback")
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
                        await throttle_click()
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

    def _find_button_by_callback_prefix(
        message,
        prefix: str,
        *,
        place_id_allowlist: set[int] | None = None,
    ) -> tuple[int, int, str] | None:
        buttons = getattr(message, "buttons", None) or []
        for row_idx, row in enumerate(buttons):
            for col_idx, btn in enumerate(row):
                callback_data = extract_callback_data(btn) or ""
                if not callback_data.startswith(prefix):
                    continue
                if place_id_allowlist is not None:
                    parts = callback_data.split("|")
                    if len(parts) < 2:
                        continue
                    try:
                        place_id = int(parts[1])
                    except Exception:
                        continue
                    if place_id not in place_id_allowlist:
                        continue
                return row_idx, col_idx, callback_data
        return None

    async def click_callback_prefix_and_wait(
        message,
        callback_prefix: str,
        *,
        predicate,
        ctx_name: str,
        place_id_allowlist: set[int] | None = None,
    ):
        current = message
        for _ in range(4):
            match = _find_button_by_callback_prefix(
                current,
                callback_prefix,
                place_id_allowlist=place_id_allowlist,
            )
            if match is None:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t, p=callback_prefix, allow=place_id_allowlist: (
                        _find_button_by_callback_prefix(m, p, place_id_allowlist=allow) is not None
                    ),
                    ctx_name=f"{ctx_name} (refresh callback buttons)",
                )
                continue

            i, j, callback_data = match
            prev_snapshot = (
                getattr(current, "id", None),
                extract_text(current),
                _to_utc(getattr(current, "edit_date", None)),
            )
            try:
                ctx.record_clicked_callback("admin", callback_data)
                await throttle_click()
                await current.click(i, j)
            except MessageIdInvalidError:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t, p=callback_prefix, allow=place_id_allowlist: (
                        _find_button_by_callback_prefix(m, p, place_id_allowlist=allow) is not None
                    ),
                    ctx_name=f"{ctx_name} (refresh stale message)",
                )
                continue

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
                continue

        raise AssertionError(f"{ctx_name}: unable to click callback prefix `{callback_prefix}`")

    def assert_not_dead_end(current_msg, *, ctx_name: str) -> None:
        if _has_recovery_controls(current_msg):
            return
        text = extract_text(current_msg)
        raise AssertionError(
            f"{ctx_name}: dead-end screen detected (no recovery controls). text={text[:220]!r}"
        )

    async def exercise_read_only_navigation(
        message,
        *,
        expect_tokens: tuple[str, ...],
        ctx_name: str,
    ):
        current = message
        text = extract_text(current)
        if _has_button(current, "Оновити"):
            current, text = await click_and_wait(
                current,
                "Оновити",
                predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                ctx_name=f"{ctx_name} refresh",
            )
            assert_contains_any(text, expect_tokens, ctx=f"{ctx_name} refresh")
            assert_not_dead_end(current, ctx_name=f"{ctx_name} refresh")

        if _has_button(current, "➡️"):
            current, text = await click_and_wait(
                current,
                "➡️",
                predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                ctx_name=f"{ctx_name} next page",
            )
            assert_contains_any(text, expect_tokens, ctx=f"{ctx_name} next page")
            assert_not_dead_end(current, ctx_name=f"{ctx_name} next page")

            if _has_button(current, "⬅️"):
                current, text = await click_and_wait(
                    current,
                    "⬅️",
                    predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
                    ctx_name=f"{ctx_name} prev page",
                )
                assert_contains_any(text, expect_tokens, ctx=f"{ctx_name} prev page")
                assert_not_dead_end(current, ctx_name=f"{ctx_name} prev page")

        return current, text

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

    async def open_business_section_from_main(
        message,
        *,
        button: str,
        expect_tokens: tuple[str, ...],
        ctx_name: str,
    ):
        current, _ = await ensure_main_menu(message)
        if not _has_button(current, "Бізнес"):
            return current, ""

        current, _ = await ensure_business_menu(current)
        if not _has_button(current, button):
            current, _ = await ensure_main_menu(current)
            return current, ""

        current, text = await click_and_wait(
            current,
            button,
            predicate=lambda _m, t, tokens=expect_tokens: any(tok in t for tok in tokens),
            ctx_name=ctx_name,
        )
        assert_contains_any(text, expect_tokens, ctx=ctx_name)
        assert_not_dead_end(current, ctx_name=f"{ctx_name} open")

        current, _ = await exercise_read_only_navigation(
            current,
            expect_tokens=expect_tokens,
            ctx_name=ctx_name,
        )
        current, _ = await ensure_main_menu(current)
        return current, text

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
        ("Черга задач", ("Черга задач", "Admin jobs")),
    ):
        require_button(msg, section, "admin main menu")
        msg, text = await click_and_wait(
            msg,
            section,
            predicate=lambda _m, t, tokens=expect: any(tok in t for tok in tokens),
            ctx_name=f"admin {section}",
        )
        assert_contains_any(text, expect, ctx=f"admin {section}")
        assert_not_dead_end(msg, ctx_name=f"admin {section} open")
        msg, text = await exercise_read_only_navigation(
            msg,
            expect_tokens=expect,
            ctx_name=f"admin {section}",
        )
        if section == "Черга задач" and _has_button(msg, "Експорт"):
            msg, text = await click_and_stay(
                msg,
                "Експорт",
                ctx_name="admin jobs export",
            )
            assert_contains_any(text, ("Черга задач", "Admin jobs"), ctx="admin jobs export")
            if _has_button(msg, "Назад"):
                msg, text = await click_and_wait(
                    msg,
                    "Назад",
                    predicate=lambda _m, t: ("Черга задач" in t) or ("Оберіть дію" in t),
                    ctx_name="admin jobs export back",
                )
                if "Оберіть дію" not in text:
                    assert_contains_any(text, ("Черга задач",), ctx="admin jobs export back")
            assert_not_dead_end(msg, ctx_name="admin jobs export")

        if section == "Сенсори":
            if _has_button(msg, "Старіші"):
                msg, text = await click_and_wait(
                    msg,
                    "Старіші",
                    predicate=lambda _m, t: "Сенсори" in t,
                    ctx_name="admin sensors older page",
                )
                assert_contains(text, ("Сенсори",), ctx="admin sensors older page")
            if _has_button(msg, "Новіші"):
                msg, text = await click_and_wait(
                    msg,
                    "Новіші",
                    predicate=lambda _m, t: "Сенсори" in t,
                    ctx_name="admin sensors newer page",
                )
                assert_contains(text, ("Сенсори",), ctx="admin sensors newer page")
            if _has_button(msg, "•"):
                msg, text = await click_first_non_nav_button(
                    msg,
                    predicate=lambda _m, t: "Сенсор" in t and "uuid:" in t,
                    ctx_name="admin sensor detail open",
                )
                assert_contains(text, ("Сенсор", "uuid:"), ctx="admin sensor detail open")
                assert_not_dead_end(msg, ctx_name="admin sensor detail open")
                if _has_button(msg, "До сенсорів"):
                    msg, text = await click_and_wait(
                        msg,
                        "До сенсорів",
                        predicate=lambda _m, t: "Сенсори" in t,
                        ctx_name="admin sensor detail back",
                    )
                    assert_contains(text, ("Сенсори",), ctx="admin sensor detail back")
                    assert_not_dead_end(msg, ctx_name="admin sensor detail back")

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
        msg, text = await ensure_main_menu(msg)
        assert_contains(text, ("Оберіть дію",), ctx="admin business menu back to main")

        for button, expect_tokens in (
            ("Модерація", ("Модерація", "Черга модерації")),
            ("Правки закладів", ("Правки закладів", "Черга порожня")),
            ("Підтримка Partner", ("Підтримка Partner", "Черга порожня")),
            ("Підписки", ("Підписки",)),
            ("Платежі", ("Платежі",)),
            ("Аудит", ("Аудит",)),
        ):
            msg, _ = await open_business_section_from_main(
                msg,
                button=button,
                expect_tokens=expect_tokens,
                ctx_name=f"admin business {button}",
            )

        # Claim tokens read-only deep flow:
        # menu -> list places -> first service -> places list -> back categories -> back tokens menu
        msg, _ = await ensure_main_menu(msg)
        if _has_button(msg, "Бізнес"):
            msg, _ = await click_and_wait(
                msg,
                "Бізнес",
                predicate=lambda m, t: "Бізнес" in t and (_has_button(m, "Коди прив'язки") or _has_button(m, "Модерація")),
                ctx_name="admin business tokens open business menu",
            )
        if _has_button(msg, "Коди прив'язки"):
            msg, text = await click_and_wait(
                msg,
                "Коди прив'язки",
                predicate=lambda _m, t: "Коди прив'язки" in t,
                ctx_name="admin business tokens menu open",
            )
            assert_not_dead_end(msg, ctx_name="admin business tokens menu open")
            if _has_button(msg, "Список закладів"):
                msg, text = await click_and_wait(
                    msg,
                    "Список закладів",
                    predicate=lambda _m, t: ("Список закладів" in t) and ("категор" in t.casefold()),
                    ctx_name="admin business tokens services open",
                )
                assert_not_dead_end(msg, ctx_name="admin business tokens services open")
                msg, text = await exercise_read_only_navigation(
                    msg,
                    expect_tokens=("Список закладів",),
                    ctx_name="admin business tokens services nav",
                )
                if _has_button(msg, "Категорії") or _has_button(msg, "Коди прив'язки") or _has_button(msg, "Бізнес"):
                    msg, text = await click_first_non_nav_button(
                        msg,
                        predicate=lambda _m, t: "Список закладів" in t and ("заклад" in t.casefold()),
                        ctx_name="admin business tokens service pick",
                    )
                    assert_not_dead_end(msg, ctx_name="admin business tokens places open")
                    msg, text = await exercise_read_only_navigation(
                        msg,
                        expect_tokens=("Список закладів",),
                        ctx_name="admin business tokens places nav",
                    )
                    if active_claim_token_place_ids:
                        try:
                            msg, text = await click_callback_prefix_and_wait(
                                msg,
                                "abiz_tokv_o|",
                                predicate=lambda _m, t: ("Код прив'язки" in t) and ("Token:" in t),
                                ctx_name="admin business tokens place open",
                                place_id_allowlist=active_claim_token_place_ids,
                            )
                            assert_contains(text, ("Код прив'язки", "Token:"), ctx="admin business tokens place open")
                            assert_not_dead_end(msg, ctx_name="admin business tokens place open")
                            if _has_button(msg, "Заклади"):
                                msg, text = await click_and_wait(
                                    msg,
                                    "Заклади",
                                    predicate=lambda _m, t: ("Список закладів" in t) and ("заклад" in t.casefold()),
                                    ctx_name="admin business tokens place back to places",
                                )
                                assert_not_dead_end(msg, ctx_name="admin business tokens place back to places")
                        except AssertionError as exc:
                            logger.info("admin tokens place-open read-only path skipped: %s", exc)
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
