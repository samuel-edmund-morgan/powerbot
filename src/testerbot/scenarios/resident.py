"""Resident bot smoke scenarios."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from telethon.errors.rpcerrorlist import DataInvalidError, FloodWaitError, MessageIdInvalidError

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
    normalized = str(label or "").strip().casefold()
    if not normalized:
        return True
    if ("меню" in normalized) or ("назад" in normalized):
        return True
    if normalized in {"⬅️", "➡️"}:
        return True
    if normalized.isdigit():
        return True
    if "/" in normalized and all(part.strip().isdigit() for part in normalized.split("/", 1)):
        return True
    return False


def _has_button(message, needle: str) -> bool:
    try:
        find_button(message, needle)
        return True
    except AssertionError:
        return False


def _has_recovery_controls(message) -> bool:
    """Best-effort check that screen has navigation/recovery controls."""
    labels: list[str] = []
    for row in (getattr(message, "buttons", None) or []):
        for btn in row:
            labels.append(str(getattr(btn, "text", "")).strip())
    # Some read-only screens can be buttonless; don't treat as dead-end.
    if not labels:
        return True
    for label in labels:
        normalized = label.casefold()
        if _is_nav_button(label):
            return True
        if (
            "оновити" in normalized
            or "сповіщення" in normalized
            or "тихі години" in normalized
            or "укриття" in normalized
            or "заклади" in normalized
            or "категор" in normalized
        ):
            return True
    return False


def _text_has_any(text: str, *needles: str) -> bool:
    hay = (text or "").casefold()
    return any((needle or "").casefold() in hay for needle in needles)


def _is_stats_screen(text: str) -> bool:
    return _text_has_any(
        text,
        "статистика",
        "uptime",
        "відключень не зафіксовано",
        "кількість відключень",
    )


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _message_activity_utc(msg) -> datetime | None:
    return _to_utc(getattr(msg, "edit_date", None) or getattr(msg, "date", None))


async def run(ctx) -> ScenarioResult:
    """Resident path (stable): /start -> building menu -> alerts -> places -> search."""
    started = time.perf_counter()
    target = ctx.cfg.targets.powerbot
    bot_id = await ctx.client.get_peer_id(target)
    scenario_started_utc = datetime.now(timezone.utc)

    async def wait_bot_message(*, predicate, ctx_name: str, previous_snapshot: tuple[int | None, str, datetime | None] | None = None):
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
                activity_utc = _message_activity_utc(msg)
                if activity_utc is not None and activity_utc < scenario_started_utc:
                    continue
                last_text = text
                if previous_snapshot is not None:
                    prev_id, prev_text, prev_edit_utc = previous_snapshot
                    same_message_id = getattr(msg, "id", None) == prev_id
                    same_text = text == prev_text
                    same_edit = _to_utc(getattr(msg, "edit_date", None)) == prev_edit_utc
                    if same_message_id and same_text and same_edit:
                        # Ignore unchanged message snapshot from before click.
                        continue
                if predicate(msg, text):
                    ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
                    return msg, text
            await asyncio.sleep(0.6)
        raise AssertionError(f"{ctx_name}: timeout waiting bot message. last_text=\n{last_text}")

    async def latest_bot_message(ctx_name: str):
        msgs = await ctx.client.get_messages(target, limit=12)
        for msg in msgs:
            if getattr(msg, "out", False):
                continue
            if getattr(msg, "sender_id", None) != bot_id:
                continue
            text = extract_text(msg)
            if not text:
                continue
            ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
            return msg, text
        raise AssertionError(f"{ctx_name}: no incoming resident-bot message found")

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
                ctx.record_clicked_callback("resident", callback_at(current, i, j))
                await current.click(i, j)
            except (MessageIdInvalidError, DataInvalidError):
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _has_button(m, needle),
                    ctx_name=f"{ctx_name} (refresh stale message)",
                )
                continue

            try:
                msg, text = await wait_bot_message(
                    predicate=predicate,
                    ctx_name=ctx_name,
                    previous_snapshot=prev_snapshot,
                )
                ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
                return msg, text
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
                        ctx.record_clicked_callback("resident", callback_at(current, row_idx, btn_idx))
                        await current.click(row_idx, btn_idx)
                    except (MessageIdInvalidError, DataInvalidError):
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
                        ctx.record_seen_callbacks("resident", collect_message_callbacks(msg))
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

    def assert_not_dead_end(current_msg, *, ctx_name: str) -> None:
        if _has_recovery_controls(current_msg):
            return
        text = extract_text(current_msg)
        raise AssertionError(
            f"{ctx_name}: dead-end screen detected (no recovery controls). text={text[:220]!r}"
        )

    async def recover_main_menu(*, ctx_name: str, allow_start_fallback: bool = False):
        # First, try to recover using the latest bot message without sending new commands.
        try:
            msg, text = await latest_bot_message(f"{ctx_name} latest")
            if ("Головне меню" in text) and _has_button(msg, "Пошук закладу"):
                return msg, text
            if _has_button(msg, "Меню"):
                msg, text = await click_and_wait(
                    msg,
                    "Меню",
                    predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
                    ctx_name=f"{ctx_name} via menu",
                )
                return msg, text
        except Exception:
            pass

        if not allow_start_fallback:
            raise AssertionError(f"{ctx_name}: unable to recover main menu without /start fallback")

        try:
            await ctx.client.send_message(target, "/start")
        except FloodWaitError as exc:
            raise AssertionError(f"{ctx_name}: flood-wait on /start recovery ({exc})") from exc
        msg, text = await wait_bot_message(
            predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Пошук закладу"),
            ctx_name=ctx_name,
        )
        return msg, text

    msg, text = await recover_main_menu(
        ctx_name="resident bootstrap main menu",
        allow_start_fallback=False,
    )
    if not _has_button(msg, "Обрати будинок"):
        msg, text = await wait_bot_message(
            predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Обрати будинок"),
            ctx_name="resident bootstrap buildings button",
        )
    assert_contains(text, ("Головне меню",), ctx="resident bootstrap")

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
    assert_not_dead_end(msg, ctx_name="resident utilities menu")

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
        predicate=lambda _m, t: _is_stats_screen(t),
        ctx_name="resident utilities stats",
    )
    assert_contains(text, ("Статистика",), ctx="resident utilities stats")

    for label in ("День", "Тиждень", "Місяць"):
        if not _has_button(msg, label):
            continue
        msg, text = await click_and_wait(
            msg,
            label,
            predicate=lambda _m, t: _is_stats_screen(t),
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
    assert_not_dead_end(msg, ctx_name="resident service menu")
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
    assert_not_dead_end(msg, ctx_name="resident notifications menu")
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
        quiet_hint = None
        for candidate in ("ℹ️ Довідка", "Довідка", "ℹ️ Інфо", "Інфо"):
            if _has_button(msg, candidate):
                quiet_hint = candidate
                break
        if quiet_hint:
            msg, text = await click_and_wait(
                msg,
                quiet_hint,
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
    assert_not_dead_end(msg, ctx_name="resident alerts menu")

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
            except Exception:
                pass

    if not _has_button(msg, "Меню") and _has_button(msg, "Назад"):
        try:
            msg, _ = await click_and_wait(
                msg,
                "Назад",
                predicate=lambda m, t: ("Тривоги та укриття" in t) and (_has_button(m, "Меню") or _has_button(m, "Назад")),
                ctx_name="resident alerts back step",
            )
        except Exception:
            msg, _ = await recover_main_menu(ctx_name="resident recover after shelters")

    if _has_button(msg, "Меню"):
        try:
            msg, _ = await click_and_wait(
                msg,
                "Меню",
                predicate=lambda m, t: ("Головне меню" in t) and _has_button(m, "Заклади в ЖК"),
                ctx_name="resident alerts back to menu",
            )
        except Exception:
            msg, _ = await recover_main_menu(ctx_name="resident recover alerts menu")
    else:
        msg, _ = await recover_main_menu(ctx_name="resident recover missing alerts menu")

    if not (("Головне меню" in extract_text(msg)) and _has_button(msg, "Заклади в ЖК")):
        msg, _ = await recover_main_menu(ctx_name="resident force main menu")

    # Places flow (read-only).
    msg, text = await click_and_wait(
        msg,
        "Заклади в ЖК",
        predicate=lambda _m, t: "Заклади в ЖК" in t,
        ctx_name="resident places menu",
    )
    assert_contains(text, ("Заклади в ЖК",), ctx="resident places menu")
    assert_contains_any(text, ("Оберіть категорію", "Поки що категорій немає"), ctx="resident places categories")
    assert_not_dead_end(msg, ctx_name="resident places menu")

    if "Поки що категорій немає" not in text:
        # Open one category.
        msg, text = await click_first_non_nav_button(
            msg,
            predicate=lambda _m, t: ("Оберіть заклад" in t) or ("Заклади в ЖК" in t),
            ctx_name="resident places category open",
        )
        assert_contains_any(text, ("Оберіть заклад", "Заклади в ЖК"), ctx="resident places category open")

        # Open one place card (if there are places in category).
        if "Оберіть заклад" in text:
            msg, text = await click_first_non_nav_button(
                msg,
                predicate=lambda m, t: (
                    ("Адреса" in t) or ("Заклад" in t) or _has_button(m, "Запропонувати правку")
                ),
                ctx_name="resident places place open",
            )
            assert_contains_any(text, ("Адреса", "Заклад"), ctx="resident places place open")

            # Cover report callback in read-only way (open -> cancel).
            if _has_button(msg, "Запропонувати правку"):
                msg, text = await click_and_wait(
                    msg,
                    "Запропонувати правку",
                    predicate=lambda m, t: ("600" in t) or _has_button(m, "Скасувати"),
                    ctx_name="resident places report open",
                )
                if _has_button(msg, "Скасувати"):
                    msg, text = await click_and_wait(
                        msg,
                        "Скасувати",
                        predicate=lambda _m, t: ("Адреса" in t) or ("Заклад" in t),
                        ctx_name="resident places report cancel",
                    )

            if _has_button(msg, "Назад"):
                msg, text = await click_and_wait(
                    msg,
                    "Назад",
                    predicate=lambda _m, t: ("Оберіть заклад" in t) or ("Заклади в ЖК" in t),
                    ctx_name="resident places place back",
                )

        if _has_button(msg, "Назад"):
            msg, text = await click_and_wait(
                msg,
                "Назад",
                predicate=lambda _m, t: "Заклади в ЖК" in t,
                ctx_name="resident places category back",
            )

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
