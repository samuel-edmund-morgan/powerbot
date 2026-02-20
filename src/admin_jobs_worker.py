import asyncio
import logging
import html
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import CFG
from admin.ui import render as render_admin_ui
from database import (
    claim_next_admin_job,
    finish_admin_job,
    update_admin_job_progress,
    db_set,
    list_subscribers,
    get_subscribers_for_offers_digest,
    mark_offers_digest_sent,
    get_all_active_sensors,
    get_building_section_power_state,
    default_section_for_building,
    freeze_sensor,
    unfreeze_sensor,
)
from services import broadcast_messages

logger = logging.getLogger(__name__)

JOB_KIND_BROADCAST = "broadcast"
JOB_KIND_OFFERS_DIGEST = "offers_digest"
JOB_KIND_LIGHT_NOTIFY = "light_notify"
JOB_KIND_ADMIN_OWNER_REQUEST_ALERT = "admin_owner_request_alert"
JOB_KIND_ADMIN_PLACE_REPORT_ALERT = "admin_place_report_alert"
JOB_KIND_SENSORS_FREEZE_ALL = "sensors_freeze_all"
JOB_KIND_SENSORS_UNFREEZE_ALL = "sensors_unfreeze_all"
SENSORS_FREEZE_FOREVER_MODE = "forever"
SENSORS_FREEZE_FOREVER_UNTIL = datetime(9999, 12, 31, 23, 59, 59)

# Best-effort cache for admin bot username used in deep-links.
_ADMIN_BOT_USERNAME_CACHE: str | None = None
_ADMIN_BOT_USERNAME_RESOLVED = False


async def _handle_light_notify(job: dict) -> None:
    desired = str(job.get("payload", {}).get("value", "")).strip().lower()
    if desired not in {"on", "off"}:
        raise ValueError("light_notify job requires payload.value = 'on'|'off'")
    await db_set("light_notifications_global", desired)


async def _handle_broadcast(bot: Bot, job: dict) -> tuple[int, int]:
    payload = job.get("payload") or {}
    raw_text = payload.get("text")
    if raw_text is None:
        raise ValueError("broadcast job requires payload.text")

    text = str(raw_text).strip()
    if not text:
        raise ValueError("broadcast job payload.text is empty")

    prefix = str(payload.get("prefix", "📢 ")).strip()
    msg_text = f"{prefix}{text}" if prefix else text

    subscribers = await list_subscribers()
    total = len(subscribers)

    job_id = int(job["id"])
    await update_admin_job_progress(job_id, current=0, total=total)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
    )

    sent_ok = 0
    sent_lock = asyncio.Lock()

    async def send_one(chat_id: int) -> None:
        nonlocal sent_ok
        # Important: do NOT use SingleMessageBot.send_message overrides (if enabled) for broadcast,
        # because it would attempt to cleanup chat state and touch DB per-recipient.
        await Bot.send_message(
            bot,
            chat_id=chat_id,
            text=msg_text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
            parse_mode=None,  # treat broadcast as plain text by default (safe for any content)
        )

        async with sent_lock:
            sent_ok += 1

    await broadcast_messages(subscribers, send_one)

    # Best-effort progress: we count only successful sends; total is recipients list size.
    await update_admin_job_progress(job_id, current=sent_ok, total=total)
    return sent_ok, total


async def _handle_offers_digest(bot: Bot, job: dict) -> tuple[int, int]:
    payload = job.get("payload") or {}
    raw_text = payload.get("text")
    if raw_text is None:
        raise ValueError("offers_digest job requires payload.text")

    text = str(raw_text).strip()
    if not text:
        raise ValueError("offers_digest job payload.text is empty")

    prefix = str(payload.get("prefix", "📬 ")).strip()
    msg_text = f"{prefix}{text}" if prefix else text
    raw_interval = payload.get("min_interval_hours")
    try:
        min_interval_hours = int(raw_interval) if raw_interval is not None else int(CFG.offers_digest_min_interval_hours)
    except Exception:
        min_interval_hours = int(CFG.offers_digest_min_interval_hours)
    min_interval_hours = max(1, min_interval_hours)

    recipients = await get_subscribers_for_offers_digest(
        current_hour=datetime.now().hour,
        min_interval_hours=min_interval_hours,
    )
    total = len(recipients)

    job_id = int(job["id"])
    await update_admin_job_progress(job_id, current=0, total=total)
    if total == 0:
        return 0, 0

    sent_ok = 0
    sent_chat_ids: list[int] = []
    sent_lock = asyncio.Lock()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
    )

    async def send_one(chat_id: int) -> None:
        nonlocal sent_ok
        await Bot.send_message(
            bot,
            chat_id=chat_id,
            text=msg_text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
            parse_mode=None,
        )
        async with sent_lock:
            sent_ok += 1
            sent_chat_ids.append(int(chat_id))

    await broadcast_messages(recipients, send_one)
    if sent_chat_ids:
        await mark_offers_digest_sent(sent_chat_ids)
    await update_admin_job_progress(job_id, current=sent_ok, total=total)
    return sent_ok, total


async def _handle_sensors_freeze_all(job: dict) -> tuple[int, int]:
    payload = job.get("payload") or {}
    mode = str(payload.get("mode") or "").strip().lower()
    is_forever = mode == SENSORS_FREEZE_FOREVER_MODE
    if is_forever:
        seconds = None
    else:
        try:
            seconds = int(payload.get("seconds", 6 * 3600))
        except Exception:
            seconds = 6 * 3600
        if seconds < 60 or seconds > 7 * 24 * 3600:
            raise ValueError("sensors_freeze_all requires payload.seconds within 60..604800")

    sensors = await get_all_active_sensors()
    total = len(sensors)
    job_id = int(job["id"])
    await update_admin_job_progress(job_id, current=0, total=total)
    if total == 0:
        return 0, 0

    now = datetime.now()
    timeout = timedelta(seconds=CFG.sensor_timeout)
    frozen_ok = 0
    for sensor in sensors:
        bid = int(sensor.get("building_id") or 0)
        sid = sensor.get("section_id") or default_section_for_building(bid) or 1
        sid = int(sid)

        section_state = await get_building_section_power_state(bid, sid)
        if section_state is not None:
            frozen_is_up = bool(section_state["is_up"])
        else:
            frozen_is_up = bool(sensor.get("last_heartbeat") and (now - sensor["last_heartbeat"]) < timeout)

        ok = await freeze_sensor(
            str(sensor["uuid"]),
            frozen_until=(SENSORS_FREEZE_FOREVER_UNTIL if is_forever else (now + timedelta(seconds=int(seconds or 0)))),
            frozen_is_up=frozen_is_up,
            frozen_at=now,
        )
        if ok:
            frozen_ok += 1
        await update_admin_job_progress(job_id, current=frozen_ok, total=total)

    return frozen_ok, total


async def _handle_sensors_unfreeze_all(job: dict) -> tuple[int, int]:
    sensors = await get_all_active_sensors()
    total = len(sensors)
    job_id = int(job["id"])
    await update_admin_job_progress(job_id, current=0, total=total)
    if total == 0:
        return 0, 0

    unfrozen_ok = 0
    for sensor in sensors:
        ok = await unfreeze_sensor(str(sensor["uuid"]))
        if ok:
            unfrozen_ok += 1
        await update_admin_job_progress(job_id, current=unfrozen_ok, total=total)
    return unfrozen_ok, total


def _build_adminbot_start_url(admin_bot_username: str | None, request_id: int) -> str | None:
    username = str(admin_bot_username or "").strip().lstrip("@")
    if not username or request_id <= 0:
        return None
    return f"https://t.me/{username}?start=bmod_{request_id}"


def _render_owner_request_alert_text(payload: dict, *, deep_link_url: str | None = None) -> str:
    request_id = int(payload.get("request_id") or 0)
    place_id = int(payload.get("place_id") or 0)
    place_name = html.escape(str(payload.get("place_name") or f"place_id={place_id}"))
    owner_tg_user_id = int(payload.get("owner_tg_user_id") or 0)
    from_label = html.escape(str(payload.get("from_label") or owner_tg_user_id))
    from_username = str(payload.get("from_username") or "").strip()
    from_first_name = str(payload.get("from_first_name") or "").strip()
    from_last_name = str(payload.get("from_last_name") or "").strip()
    from_full_name = str(payload.get("from_full_name") or "").strip()
    full_name = from_full_name or " ".join(part for part in [from_first_name, from_last_name] if part).strip()
    if from_username:
        owner_label = f"@{html.escape(from_username)}"
    elif full_name:
        owner_label = html.escape(full_name)
    else:
        owner_label = from_label
    owner_link = f'<a href="tg://user?id={owner_tg_user_id}">{owner_label}</a>' if owner_tg_user_id > 0 else owner_label
    source = html.escape(str(payload.get("source") or "unknown"))
    created_at = html.escape(str(payload.get("created_at") or ""))
    text = (
        "🛎 Нова заявка власника бізнесу\n\n"
        f"Заявка: <code>{request_id}</code>\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{place_id}</code>)\n"
        f"Власник: {owner_link} / <code>{owner_tg_user_id}</code>\n"
        f"Джерело: <code>{source}</code>\n"
        f"Створено: {created_at}"
        "\n\n⚙️ Модерація: відкрий <b>adminbot</b> → <b>Бізнес</b> → <b>Модерація</b>."
    )
    if deep_link_url:
        safe_url = html.escape(deep_link_url, quote=True)
        text += f"\n🔗 Швидкий перехід: <a href=\"{safe_url}\">відкрити заявку</a>."
    return text


def _owner_request_alert_keyboard(*, request_id: int, deep_link_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🛡 Відкрити заявку", callback_data=f"abiz_mod_jump|{int(request_id)}")],
        [InlineKeyboardButton(text="🧭 Вся модерація", callback_data="abiz_mod")],
    ]
    if deep_link_url:
        rows.append([InlineKeyboardButton(text="🔗 Deep-link", url=deep_link_url)])
    rows.append([InlineKeyboardButton(text="🏠 Головне меню", callback_data="admin_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_place_report_alert_text(payload: dict, *, deep_link_url: str | None = None) -> str:
    report_id = int(payload.get("report_id") or 0)
    place_id = int(payload.get("place_id") or 0)
    place_name = html.escape(str(payload.get("place_name") or f"place_id={place_id}"))
    reporter_tg_user_id = int(payload.get("reporter_tg_user_id") or 0)
    reporter_username = str(payload.get("reporter_username") or "").strip()
    reporter_first_name = str(payload.get("reporter_first_name") or "").strip()
    reporter_last_name = str(payload.get("reporter_last_name") or "").strip()
    full_name = " ".join(part for part in [reporter_first_name, reporter_last_name] if part).strip()
    if reporter_username:
        reporter_label = f"@{html.escape(reporter_username)}"
    elif full_name:
        reporter_label = html.escape(full_name)
    else:
        reporter_label = html.escape(str(reporter_tg_user_id))
    if reporter_tg_user_id > 0:
        reporter_link = f'<a href="tg://user?id={reporter_tg_user_id}">{reporter_label}</a>'
    else:
        reporter_link = reporter_label
    report_text = html.escape(str(payload.get("report_text") or "").strip())
    created_at = html.escape(str(payload.get("created_at") or ""))
    text = (
        "📝 Нова правка до картки закладу\n\n"
        f"Репорт: <code>{report_id}</code>\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{place_id}</code>)\n"
        f"Від: {reporter_link} / <code>{reporter_tg_user_id}</code>\n"
        f"Створено: {created_at}\n\n"
        f"Текст:\n{report_text}\n\n"
        "⚙️ Перевір в <b>adminbot</b> → <b>Бізнес</b> → <b>Правки закладів</b>."
    )
    if deep_link_url:
        safe_url = html.escape(deep_link_url, quote=True)
        text += f"\n🔗 Швидкий перехід: <a href=\"{safe_url}\">відкрити репорт</a>."
    return text


def _place_report_alert_keyboard(*, report_id: int, deep_link_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📝 Відкрити репорт", callback_data=f"abiz_reports_jump|{int(report_id)}")],
        [InlineKeyboardButton(text="🧭 Всі правки", callback_data="abiz_reports")],
    ]
    if deep_link_url:
        rows.append([InlineKeyboardButton(text="🔗 Deep-link", url=deep_link_url)])
    rows.append([InlineKeyboardButton(text="🏠 Головне меню", callback_data="admin_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _resolve_admin_bot_username(bot: Bot) -> str | None:
    global _ADMIN_BOT_USERNAME_CACHE, _ADMIN_BOT_USERNAME_RESOLVED
    if _ADMIN_BOT_USERNAME_RESOLVED:
        return _ADMIN_BOT_USERNAME_CACHE
    _ADMIN_BOT_USERNAME_RESOLVED = True
    try:
        me = await bot.get_me()
        username = str(getattr(me, "username", "") or "").strip()
        _ADMIN_BOT_USERNAME_CACHE = username or None
    except Exception:
        logger.exception("Failed to resolve admin bot username for deep-link")
        _ADMIN_BOT_USERNAME_CACHE = None
    return _ADMIN_BOT_USERNAME_CACHE


async def _handle_admin_place_report_alert(job: dict) -> tuple[int, int]:
    payload = job.get("payload") or {}
    report_id = int(payload.get("report_id") or 0)
    place_id = int(payload.get("place_id") or 0)
    reporter_tg_user_id = int(payload.get("reporter_tg_user_id") or 0)
    if report_id <= 0 or place_id <= 0 or reporter_tg_user_id <= 0:
        raise ValueError("admin_place_report_alert requires report_id/place_id/reporter_tg_user_id")
    if not CFG.admin_ids:
        raise ValueError("admin_place_report_alert requires non-empty ADMIN_IDS")
    if not (CFG.admin_bot_api_key or "").strip():
        raise ValueError("admin_place_report_alert requires non-empty ADMIN_BOT_API_KEY")

    total = len(CFG.admin_ids)
    sent_ok = 0

    admin_bot = Bot(
        token=CFG.admin_bot_api_key,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        admin_bot_username = await _resolve_admin_bot_username(admin_bot)
        safe_username = str(admin_bot_username or "").strip().lstrip("@")
        deep_link_url = f"https://t.me/{safe_username}?start=brep_{report_id}" if safe_username else None
        text = _render_place_report_alert_text(payload, deep_link_url=deep_link_url)
        kb = _place_report_alert_keyboard(report_id=report_id, deep_link_url=deep_link_url)
        for admin_id in CFG.admin_ids:
            try:
                await render_admin_ui(
                    admin_bot,
                    chat_id=int(admin_id),
                    text=text,
                    reply_markup=kb,
                    force_new_message=True,
                )
                sent_ok += 1
            except Exception:
                logger.exception(
                    "Failed to send admin place-report alert to admin_id=%s report_id=%s",
                    admin_id,
                    report_id,
                )
    finally:
        try:
            await admin_bot.session.close()
        except Exception:
            pass

    return sent_ok, total


async def _handle_admin_owner_request_alert(job: dict) -> tuple[int, int]:
    payload = job.get("payload") or {}
    request_id = int(payload.get("request_id") or 0)
    place_id = int(payload.get("place_id") or 0)
    owner_tg_user_id = int(payload.get("owner_tg_user_id") or 0)
    if request_id <= 0 or place_id <= 0 or owner_tg_user_id <= 0:
        raise ValueError("admin_owner_request_alert requires request_id/place_id/owner_tg_user_id")
    if not CFG.admin_ids:
        raise ValueError("admin_owner_request_alert requires non-empty ADMIN_IDS")
    if not (CFG.admin_bot_api_key or "").strip():
        raise ValueError("admin_owner_request_alert requires non-empty ADMIN_BOT_API_KEY")

    total = len(CFG.admin_ids)
    sent_ok = 0

    admin_bot = Bot(
        token=CFG.admin_bot_api_key,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        admin_bot_username = await _resolve_admin_bot_username(admin_bot)
        deep_link_url = _build_adminbot_start_url(admin_bot_username, request_id)
        text = _render_owner_request_alert_text(payload, deep_link_url=deep_link_url)
        kb = _owner_request_alert_keyboard(request_id=request_id, deep_link_url=deep_link_url)
        for admin_id in CFG.admin_ids:
            try:
                # Keep admin chat clean: owner-request alerts should behave like single-message UI.
                await render_admin_ui(
                    admin_bot,
                    chat_id=int(admin_id),
                    text=text,
                    reply_markup=kb,
                    force_new_message=True,
                )
                sent_ok += 1
            except Exception:
                logger.exception(
                    "Failed to send admin owner request alert to admin_id=%s request_id=%s",
                    admin_id,
                    request_id,
                )
    finally:
        try:
            await admin_bot.session.close()
        except Exception:
            pass

    return sent_ok, total


async def admin_jobs_worker_loop(bot: Bot, *, poll_interval_sec: float = 1.0) -> None:
    """Background worker that executes jobs enqueued by the admin bot."""
    sleep_s = max(0.2, float(poll_interval_sec))
    while True:
        try:
            job = await claim_next_admin_job()
            if not job:
                await asyncio.sleep(sleep_s)
                continue

            job_id = int(job["id"])
            try:
                kind = str(job.get("kind") or "").strip()
                if kind == JOB_KIND_LIGHT_NOTIFY:
                    await _handle_light_notify(job)
                    await update_admin_job_progress(job_id, current=1, total=1)
                    done_current, done_total = 1, 1
                elif kind == JOB_KIND_BROADCAST:
                    done_current, done_total = await _handle_broadcast(bot, job)
                elif kind == JOB_KIND_OFFERS_DIGEST:
                    done_current, done_total = await _handle_offers_digest(bot, job)
                elif kind == JOB_KIND_ADMIN_OWNER_REQUEST_ALERT:
                    done_current, done_total = await _handle_admin_owner_request_alert(job)
                elif kind == JOB_KIND_ADMIN_PLACE_REPORT_ALERT:
                    done_current, done_total = await _handle_admin_place_report_alert(job)
                elif kind == JOB_KIND_SENSORS_FREEZE_ALL:
                    done_current, done_total = await _handle_sensors_freeze_all(job)
                elif kind == JOB_KIND_SENSORS_UNFREEZE_ALL:
                    done_current, done_total = await _handle_sensors_unfreeze_all(job)
                else:
                    raise ValueError(f"Unknown admin job kind: {kind}")

                await finish_admin_job(
                    job_id,
                    status="done",
                    progress_current=done_current,
                    progress_total=done_total,
                )
            except Exception as exc:
                logger.exception("Admin job failed id=%s kind=%s", job_id, job.get("kind"))
                await finish_admin_job(job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("Admin jobs worker tick failed")
            await asyncio.sleep(2.0)
