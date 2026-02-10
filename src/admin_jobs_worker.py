import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import (
    claim_next_admin_job,
    finish_admin_job,
    update_admin_job_progress,
    db_set,
    list_subscribers,
)
from services import broadcast_messages

logger = logging.getLogger(__name__)

JOB_KIND_BROADCAST = "broadcast"
JOB_KIND_LIGHT_NOTIFY = "light_notify"


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
