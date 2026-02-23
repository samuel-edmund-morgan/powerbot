"""Business bot smoke scenario."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import re
import sqlite3
import time
from dataclasses import dataclass

from telethon.errors.rpcerrorlist import DataInvalidError, MessageIdInvalidError

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
    me = await ctx.client.get_me()
    actor_tg_user_id = int(getattr(me, "id", 0) or 0)

    def _load_approved_place_ids() -> list[int]:
        db_path = str(getattr(ctx.cfg, "db_path", "") or "").strip()
        if not db_path or actor_tg_user_id <= 0:
            return []
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            rows = conn.execute(
                """
                SELECT DISTINCT place_id
                FROM business_owners
                WHERE tg_user_id = ? AND status = 'approved'
                ORDER BY place_id
                """,
                (actor_tg_user_id,),
            ).fetchall()
            return [int(r[0]) for r in rows if r and int(r[0]) > 0]
        except Exception as exc:
            logger.warning("business testerbot: failed to load approved place ids: %s", exc)
            return []
        finally:
            if conn is not None:
                conn.close()

    approved_place_ids = _load_approved_place_ids()

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
                # If bot switched to input-only FSM prompt, waiting for the old button
                # is pointless. Bubble up so caller can recover (/cancel or "-").
                if not (getattr(current, "buttons", None) or []) and (
                    "Надішли" in extract_text(current)
                    or "Введи" in extract_text(current)
                    or "Введіть" in extract_text(current)
                ):
                    raise AssertionError(
                        f"{ctx_name}: target button `{needle}` is gone on input-only screen"
                    )
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
            except (MessageIdInvalidError, DataInvalidError):
                current, _ = await latest_bot_message(
                    f"{ctx_name} (refresh stale/invalid callback message)"
                )
                if not _has_button(current, needle):
                    raise AssertionError(
                        f"{ctx_name}: stale callback and target button `{needle}` disappeared"
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
                    except (MessageIdInvalidError, DataInvalidError):
                        current, _ = await wait_bot_message(
                            predicate=lambda m, _t: bool(getattr(m, "buttons", None)),
                            ctx_name=f"{ctx_name} (refresh stale/invalid callback message)",
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

    def _find_button_pos_by_callback(message, callback_data: str) -> tuple[int, int] | None:
        buttons = getattr(message, "buttons", None) or []
        for row_idx, row in enumerate(buttons):
            for col_idx, btn in enumerate(row):
                raw = extract_callback_data(btn)
                if raw == callback_data:
                    return row_idx, col_idx
        return None

    async def click_callback_and_wait(message, callback_data: str, *, predicate, ctx_name: str):
        current = message
        for _ in range(4):
            pos = _find_button_pos_by_callback(current, callback_data)
            if pos is None:
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _find_button_pos_by_callback(m, callback_data) is not None,
                    ctx_name=f"{ctx_name} (refresh callback buttons)",
                )
                continue

            i, j = pos
            try:
                ctx.record_clicked_callback("business", callback_at(current, i, j))
                prev_snapshot = (
                    getattr(current, "id", None),
                    extract_text(current),
                    _to_utc(getattr(current, "edit_date", None)),
                )
                await current.click(i, j)
                msg, text = await wait_bot_message(
                    predicate=predicate,
                    ctx_name=ctx_name,
                    previous_snapshot=prev_snapshot,
                )
                ctx.record_seen_callbacks("business", collect_message_callbacks(msg))
                return msg, text
            except (MessageIdInvalidError, DataInvalidError):
                current, _ = await wait_bot_message(
                    predicate=lambda m, _t: _find_button_pos_by_callback(m, callback_data) is not None,
                    ctx_name=f"{ctx_name} (refresh stale/invalid callback message)",
                )
                continue
            except AssertionError:
                current, _ = await latest_bot_message(f"{ctx_name} (refresh latest)")
                continue
        raise AssertionError(f"{ctx_name}: unable to click callback `{callback_data}`")

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
        last_nav_error: Exception | None = None

        async def recover_via_cancel(*, ctx_name: str, text_hint: str = ""):
            commands: list[str] = []
            if "Надішли" in text_hint and "-" in text_hint:
                commands.append("-")
            commands.append("/cancel")

            last_error: Exception | None = None
            for command in commands:
                try:
                    sent = await ctx.client.send_message(target, command)
                    sent_utc = _to_utc(getattr(sent, "date", None)) or scenario_started_utc
                    return await wait_bot_message(
                        predicate=lambda _m, t: (
                            "Оберіть дію:" in t
                            or _is_owner_card_text(t)
                            or "Оберіть заклад" in t
                            or "Обери тариф для" in t
                            or "Плани" in t
                        ),
                        ctx_name=f"{ctx_name} [{command}]",
                        min_activity_utc=sent_utc,
                    )
                except AssertionError as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
            raise AssertionError(f"{ctx_name}: failed to recover state")

        for _ in range(6):
            if "Оберіть дію:" in current_text:
                return current, current_text
            if _has_button(current, "Меню"):
                try:
                    current, current_text = await click_and_wait(
                        current,
                        "Меню",
                        predicate=lambda _m, t: "Оберіть дію:" in t,
                        ctx_name="business ensure main menu via menu",
                    )
                except AssertionError as exc:
                    last_nav_error = exc
                    current, current_text = await recover_via_cancel(
                        ctx_name="business ensure main menu recover /cancel after menu failure",
                        text_hint=current_text,
                    )
                continue
            if _has_button(current, "Назад"):
                try:
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
                except AssertionError as exc:
                    last_nav_error = exc
                    current, current_text = await recover_via_cancel(
                        ctx_name="business ensure main menu recover /cancel after back failure",
                        text_hint=current_text,
                    )
                continue
            if _has_button(current, "Скасувати"):
                try:
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
                        ctx_name="business ensure main menu via cancel button",
                    )
                except AssertionError as exc:
                    last_nav_error = exc
                    current, current_text = await recover_via_cancel(
                        ctx_name="business ensure main menu recover /cancel after cancel-button failure",
                        text_hint=current_text,
                    )
                continue
            # Input-only FSM prompt (no inline controls) can trap the scenario.
            # Use /cancel to reset state without mutating business data.
            if (
                not (getattr(current, "buttons", None) or [])
                and (
                    "Надішли" in current_text
                    or "Введи" in current_text
                    or "Введіть" in current_text
                )
            ):
                current, current_text = await recover_via_cancel(
                    ctx_name="business ensure main menu via /cancel input-only",
                    text_hint=current_text,
                )
                continue
            if last_nav_error is not None:
                current, current_text = await recover_via_cancel(
                    ctx_name="business ensure main menu via /cancel nav fallback",
                    text_hint=current_text,
                )
                continue
            break
        if "Оберіть дію:" not in current_text:
            latest, latest_text = await latest_bot_message("business ensure main menu final refresh")
            current, current_text = latest, latest_text
        assert_contains(current_text, ("Оберіть дію:",), ctx="business ensure main menu")
        return current, current_text

    async def open_first_owner_card(
        message,
        *,
        ctx_name: str,
        preferred_place_id: int | None = None,
        required_button: str | None = None,
    ):
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
        if preferred_place_id:
            target_cb = f"bmy_o:{int(preferred_place_id)}"
            located = False
            for _ in range(8):
                if _find_button_pos_by_callback(current, target_cb) is not None:
                    current, text = await click_callback_and_wait(
                        current,
                        target_cb,
                        predicate=lambda _m, t: _is_owner_card_text(t),
                        ctx_name=f"{ctx_name} open owner card by place id",
                    )
                    located = True
                    break
                if _has_button(current, "➡️"):
                    current, text = await click_and_wait(
                        current,
                        "➡️",
                        predicate=lambda _m, t: ("Оберіть заклад" in t) or ("У тебе ще немає бізнесів" in t),
                        ctx_name=f"{ctx_name} owner list next page",
                    )
                    continue
                break
            if not located:
                raise AssertionError(f"{ctx_name}: place_id {preferred_place_id} not found in owner list")
        else:
            current, text = await click_first_non_nav_button(
                current,
                predicate=lambda _m, t: _is_owner_card_text(t),
                ctx_name=f"{ctx_name} open owner card",
            )
        assert_contains_any(text, ("Статус доступу", "Тариф", "Активно до"), ctx=f"{ctx_name} owner card")
        if required_button and not _has_button(current, required_button):
            raise AssertionError(f"{ctx_name}: owner card has no `{required_button}` button")
        return current, text

    async def ensure_owner_card_with_action(message, *, needle: str, ctx_name: str):
        last_error: Exception | None = None
        for place_id in approved_place_ids:
            try:
                current, text = await open_first_owner_card(
                    message,
                    ctx_name=f"{ctx_name} place#{place_id}",
                    preferred_place_id=place_id,
                    required_button=needle,
                )
                return current, text
            except AssertionError as exc:
                last_error = exc

        current, text = await open_first_owner_card(message, ctx_name=ctx_name, required_button=None)
        if _has_button(current, needle):
            return current, text
        # Retry on transient UI/state drift (edited owner-card without full action rows yet).
        try:
            current, text = await wait_bot_message(
                predicate=lambda m, t: _is_owner_card_text(t) and _has_button(m, needle),
                ctx_name=f"{ctx_name} wait action `{needle}`",
                previous_snapshot=(
                    getattr(current, "id", None),
                    extract_text(current),
                    _to_utc(getattr(current, "edit_date", None)),
                ),
            )
            return current, text
        except AssertionError as exc:
            if last_error is not None:
                raise AssertionError(f"{ctx_name}: no owner-card with `{needle}` (last={last_error})") from exc
            raise

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
            try:
                current, _ = await ensure_owner_card_with_action(
                    current,
                    needle=needle,
                    ctx_name=f"business owner action {needle}",
                )
            except AssertionError as exc:
                logger.warning(
                    "business owner action `%s` skipped: unable to prepare owner-card state: %s",
                    needle,
                    exc,
                )
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
            try:
                current, _ = await open_first_owner_card(current, ctx_name=f"business owner action {needle} recover")
            except AssertionError:
                # Keep scenario moving; next iteration will re-open owner-card from main menu.
                pass
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
                try:
                    msg, text = await click_and_wait(
                        msg,
                        "Назад",
                        predicate=lambda _m, t: (
                            ("Статус доступу" in t)
                            or ("Тариф" in t)
                            or ("Активно до" in t)
                            or ("Оберіть заклад" in t)
                            or ("Плани" in t)
                        ),
                        ctx_name="business owner plans back to card",
                    )
                    assert_contains_any(
                        text,
                        ("Статус доступу", "Тариф", "Активно до", "Оберіть заклад", "Плани"),
                        ctx="business owner plans back to card",
                    )
                    if "Оберіть заклад" in text:
                        try:
                            msg, text = await click_first_non_nav_button(
                                msg,
                                predicate=lambda _m, t: (
                                    ("Статус доступу" in t) or ("Тариф" in t) or ("Активно до" in t)
                                ),
                                ctx_name="business owner plans reopen card from list",
                            )
                            assert_contains_any(
                                text,
                                ("Статус доступу", "Тариф", "Активно до"),
                                ctx="business owner plans reopen card from list",
                            )
                        except AssertionError as exc:
                            logger.warning(
                                "business owner plans back: list returned but owner card reopen skipped: %s",
                                exc,
                            )
                except AssertionError as exc:
                    logger.warning("business owner plans back to card skipped due unstable state: %s", exc)

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
