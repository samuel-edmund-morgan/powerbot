import asyncio
import json
import logging
import os
import hashlib
from datetime import datetime
from typing import Any

import aiohttp

from config import CFG
from database import db_get, db_set, get_building_by_id


logger = logging.getLogger(__name__)

_BASE_URL = "https://app.yasno.ua/api/blackout-service/public/shutdowns"
_PLANNED_OUTAGES_KEY = "yasno:planned_outages"
_PLANNED_OUTAGES_TTL = 600  # 10 хвилин
_ADDRESS_CACHE_TTL = 6 * 3600  # 6 годин
_LAST_CONFIG: dict[int, tuple[str | None, tuple[str, ...]]] = {}


def _now_ts() -> float:
    return datetime.now().timestamp()


def _parse_cached(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _fetch_json(url: str, params: dict, timeout: int = 12):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Yasno API returned {resp.status}")
            return await resp.json()


def _building_env_prefix(building_id: int) -> str:
    return f"YASNO_BUILDING_{building_id}"


def _parse_queries(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip().strip('"').strip("'")
    if not value:
        return []
    parts: list[str] = []
    for chunk in value.replace("|", ",").split(","):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _get_building_queries(building_id: int) -> tuple[str | None, list[str]]:
    prefix = _building_env_prefix(building_id)
    street_query = os.getenv(f"{prefix}_STREET_QUERY")
    house_queries_raw = os.getenv(f"{prefix}_HOUSE_QUERIES")
    if street_query:
        street_query = street_query.strip().strip('"').strip("'")
    house_queries = _parse_queries(house_queries_raw)
    if not street_query:
        street_query = os.getenv("YASNO_STREET_QUERY_DEFAULT", "").strip().strip('"').strip("'")
    if not house_queries:
        return None, []
    return street_query or None, house_queries


def _log_config_if_changed(building_id: int, street_query: str | None, house_queries: list[str]) -> None:
    current = (street_query, tuple(house_queries))
    previous = _LAST_CONFIG.get(building_id)
    if previous == current:
        return
    _LAST_CONFIG[building_id] = current
    logger.info(
        "yasno: config building=%s street_query=%s house_queries=%s",
        building_id,
        street_query,
        "|".join(house_queries),
    )


async def get_planned_outages(force: bool = False) -> dict | None:
    if not CFG.yasno_enabled:
        return None
    cached = _parse_cached(await db_get(_PLANNED_OUTAGES_KEY))
    now = _now_ts()
    if not force and cached and now - cached.get("ts", 0) < _PLANNED_OUTAGES_TTL:
        return cached.get("data")

    try:
        data = await _fetch_json(
            f"{_BASE_URL}/regions/{CFG.yasno_region_id}/dsos/{CFG.yasno_dso_id}/planned-outages",
            params={},
            timeout=15,
        )
        await db_set(_PLANNED_OUTAGES_KEY, json.dumps({"ts": now, "data": data}))
        return data
    except Exception as exc:
        logger.warning("Failed to fetch planned outages: %s", exc)
        if cached:
            return cached.get("data")
        return None


async def _get_street_id(street_query: str) -> int | None:
    cache_key = f"yasno:street:{CFG.yasno_region_id}:{CFG.yasno_dso_id}:{street_query}"
    cached = _parse_cached(await db_get(cache_key))
    now = _now_ts()
    if cached and now - cached.get("ts", 0) < _ADDRESS_CACHE_TTL:
        return cached.get("id")

    try:
        data = await _fetch_json(
            f"{_BASE_URL}/addresses/v2/streets",
            params={
                "regionId": CFG.yasno_region_id,
                "query": street_query,
                "dsoId": CFG.yasno_dso_id,
            },
        )
        if data:
            street_id = data[0].get("id")
            if street_id:
                await db_set(cache_key, json.dumps({"ts": now, "id": int(street_id)}))
                return int(street_id)
    except Exception as exc:
        logger.warning("Failed to fetch street id (%s): %s", street_query, exc)
        if cached:
            return cached.get("id")
    return None


async def _get_house_ids(street_id: int, house_query: str) -> list[dict]:
    cache_key = f"yasno:house:{CFG.yasno_region_id}:{CFG.yasno_dso_id}:{street_id}:{house_query}"
    cached = _parse_cached(await db_get(cache_key))
    now = _now_ts()
    if cached and now - cached.get("ts", 0) < _ADDRESS_CACHE_TTL:
        return cached.get("houses", [])

    try:
        data = await _fetch_json(
            f"{_BASE_URL}/addresses/v2/houses",
            params={
                "regionId": CFG.yasno_region_id,
                "streetId": street_id,
                "query": house_query,
                "dsoId": CFG.yasno_dso_id,
            },
        )
        houses = [
            {"id": int(item.get("id")), "value": item.get("value")}
            for item in data or []
            if item.get("id")
        ]
        await db_set(cache_key, json.dumps({"ts": now, "houses": houses}))
        return houses
    except Exception as exc:
        logger.warning("Failed to fetch house ids (%s): %s", house_query, exc)
        if cached:
            return cached.get("houses", [])
    return []


async def _get_group_info(street_id: int, house_id: int) -> dict | None:
    cache_key = f"yasno:group:{CFG.yasno_region_id}:{CFG.yasno_dso_id}:{street_id}:{house_id}"
    cached = _parse_cached(await db_get(cache_key))
    now = _now_ts()
    if cached and now - cached.get("ts", 0) < _ADDRESS_CACHE_TTL:
        return cached.get("group")

    try:
        data = await _fetch_json(
            f"{_BASE_URL}/addresses/v2/group",
            params={
                "regionId": CFG.yasno_region_id,
                "streetId": street_id,
                "houseId": house_id,
                "dsoId": CFG.yasno_dso_id,
            },
        )
        if data and data.get("group") is not None and data.get("subgroup") is not None:
            await db_set(cache_key, json.dumps({"ts": now, "group": data}))
            return data
    except Exception as exc:
        logger.warning("Failed to fetch group info house_id=%s: %s", house_id, exc)
        if cached:
            return cached.get("group")
    return None


def _extract_definite_ranges(slots: list[dict]) -> list[tuple[int, int]]:
    ranges = []
    for slot in slots or []:
        if slot.get("type") != "Definite":
            continue
        start = int(slot.get("start", 0))
        end = int(slot.get("end", 0))
        if end <= start:
            continue
        ranges.append((start, end))
    return ranges


def _format_day(outage: dict | None) -> tuple[str, str]:
    if not outage:
        return "", "немає даних"
    date_raw = outage.get("date")
    date_label = ""
    if date_raw:
        try:
            date_label = datetime.fromisoformat(date_raw).strftime("%d.%m")
        except Exception:
            date_label = ""
    status = outage.get("status")
    if status != "ScheduleApplies":
        if status == "EmergencyShutdowns":
            return date_label, "екстрені відключення"
        if status == "NoShutdowns":
            return date_label, "відключень не очікується"
        if status == "NoData":
            return date_label, "немає даних"
        return date_label, "немає даних"
    slots = outage.get("slots", [])
    ranges = _extract_definite_ranges(slots)
    if not ranges:
        return date_label, "немає відключень"

    def fmt(minutes: int) -> str:
        h = minutes // 60
        m = minutes % 60
        return f"{h:02d}:{m:02d}"

    return date_label, ", ".join(f"{fmt(s)}–{fmt(e)}" for s, e in ranges)


def _status_has_data(status: str | None) -> bool:
    if not status:
        return False
    return status not in {"NoData"}


def _hash_outage(outage: dict | None) -> str:
    if not outage:
        return "none"
    status = outage.get("status") or ""
    slots = outage.get("slots") or []
    norm_slots = [
        {
            "start": int(slot.get("start", 0)),
            "end": int(slot.get("end", 0)),
            "type": str(slot.get("type", "")),
        }
        for slot in slots
    ]
    payload = {"status": status, "slots": norm_slots}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _day_key(label: str, outage: dict | None) -> str:
    date_raw = outage.get("date") if outage else None
    date_key = ""
    if date_raw:
        date_key = str(date_raw).split("T")[0]
    return f"{label}:{date_key or 'unknown'}"


def _format_schedule_text(data: dict[str, Any], include_building: bool = True) -> str:
    queues = data["queues"]
    lines = ["🗓 <b>Орієнтовні графіки</b>"]
    building = data.get("building")
    if include_building and building:
        lines.append(f"🏠 {building['name']} ({building['address']})")

    for queue in queues:
        today_label, today_text = _format_day(queue["data"].get("today") if queue["data"] else None)
        tomorrow_label, tomorrow_text = _format_day(queue["data"].get("tomorrow") if queue["data"] else None)
        lines.append("")
        lines.append(f"<b>{queue['label']}</b> • черга {queue['key']}")
        if today_label:
            lines.append(f"Сьогодні ({today_label}): {today_text}")
        else:
            lines.append(f"Сьогодні: {today_text}")
        if tomorrow_label:
            lines.append(f"Завтра ({tomorrow_label}): {tomorrow_text}")
        else:
            lines.append(f"Завтра: {tomorrow_text}")

    return "\n".join(lines)


async def _get_building_schedule_data(
    building_id: int,
    *,
    log_context: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    if not CFG.yasno_enabled:
        return None, "ℹ️ Графіки не ввімкнені."
    street_query, house_queries = _get_building_queries(building_id)
    if not street_query or not house_queries:
        return None, "ℹ️ Графіки для цього будинку не налаштовані."
    _log_config_if_changed(building_id, street_query, house_queries)

    planned = await get_planned_outages()
    if not planned:
        return None, "⚠️ Дані про графіки від ЯСНО недоступні."

    street_id = await _get_street_id(street_query)
    if not street_id:
        return None, "⚠️ Не вдалося отримати адресу для графіків."
    if log_context:
        logger.info(
            "yasno: building=%s street_query=%s street_id=%s house_queries=%s",
            building_id,
            street_query,
            street_id,
            "|".join(house_queries),
        )

    queues = []
    seen_house_ids: set[int] = set()
    for house_query in house_queries:
        houses = await _get_house_ids(street_id, house_query)
        for house in houses:
            house_id = house.get("id")
            if not house_id or house_id in seen_house_ids:
                continue
            seen_house_ids.add(house_id)
            group_info = await _get_group_info(street_id, house_id)
            if not group_info:
                continue
            group_key = f"{group_info['group']}.{group_info['subgroup']}"
            if log_context:
                logger.info(
                    "yasno: building=%s house_query=%s house_id=%s group=%s",
                    building_id,
                    house.get("value") or house_query,
                    house_id,
                    group_key,
                )
            queues.append({
                "key": group_key,
                "label": house.get("value") or house_query,
                "data": planned.get(group_key),
            })

    if not queues:
        return None, "⚠️ Графіки для цього будинку недоступні."

    building = get_building_by_id(building_id)
    return {"building": building, "queues": queues}, None


async def get_building_schedule_text(building_id: int, include_building: bool = True) -> str:
    data, error = await _get_building_schedule_data(building_id)
    if error:
        return error
    return _format_schedule_text(data, include_building=include_building)



async def planned_outages_loop() -> None:
    if not CFG.yasno_enabled:
        return
    while True:
        try:
            await get_planned_outages(force=True)
        except Exception:
            logger.exception("planned_outages_loop error")
        await asyncio.sleep(_PLANNED_OUTAGES_TTL)


async def yasno_schedule_monitor_loop(bot) -> None:
    """Оновлює кеш графіків і надсилає сповіщення про зміни."""
    if not CFG.yasno_enabled:
        return
    from database import (
        BUILDINGS,
        get_subscribers_for_schedule_notification,
        get_yasno_schedule_state,
        upsert_yasno_schedule_state,
        get_building_by_id,
        get_active_notifications,
        delete_notification,
        save_notification,
        get_last_bot_message,
        delete_last_bot_message_record,
    )
    from services import broadcast_messages
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    def _status_has_data(status: str | None) -> bool:
        return status not in (None, "", "NoData")

    async def _detect_changes(building_id: int, data: dict[str, Any]) -> dict:
        changes = {
            "today_changed": False,
            "tomorrow_changed": False,
            "tomorrow_available": False,
            "emergency": False,
        }
        now_iso = datetime.now().isoformat()
        for queue in data["queues"]:
            queue_key = queue["key"]
            for label in ("today", "tomorrow"):
                outage = queue["data"].get(label) if queue["data"] else None
                day_key = _day_key(label, outage)
                status = outage.get("status") if outage else None
                slots_hash = _hash_outage(outage)
                prev = await get_yasno_schedule_state(building_id, queue_key, day_key)
                if prev:
                    if prev["status"] != status or prev["slots_hash"] != slots_hash:
                        if label == "today":
                            changes["today_changed"] = True
                        else:
                            changes["tomorrow_changed"] = True
                            if not _status_has_data(prev["status"]) and _status_has_data(status):
                                changes["tomorrow_available"] = True
                        if status == "EmergencyShutdowns" and prev["status"] != "EmergencyShutdowns":
                            changes["emergency"] = True
                await upsert_yasno_schedule_state(
                    building_id,
                    queue_key,
                    day_key,
                    status,
                    slots_hash,
                    now_iso,
                )
        return changes

    while True:
        try:
            await get_planned_outages(force=True)
            current_hour = datetime.now().hour

            for building in BUILDINGS:
                building_id = building["id"]
                data, error = await _get_building_schedule_data(building_id, log_context=True)
                if error or not data:
                    continue

                changes = await _detect_changes(building_id, data)
                if not any(changes.values()):
                    continue
                logger.info(
                    "yasno: schedule change building=%s today=%s tomorrow=%s emergency=%s",
                    building_id,
                    changes["today_changed"],
                    changes["tomorrow_changed"],
                    changes["emergency"],
                )

                header_lines = ["🗓 <b>Оновлення графіків</b>"]
                b = get_building_by_id(building_id)
                if b:
                    header_lines.append(f"🏠 {b['name']} ({b['address']})")

                if changes["emergency"]:
                    header_lines.append("⚠️ Увага! Графіки позначені як екстрені відключення.")
                else:
                    if changes["tomorrow_changed"]:
                        header_lines.append("📅 Зʼявились або оновились орієнтовні графіки на завтра.")
                    if changes["today_changed"]:
                        header_lines.append("🔄 Сьогоднішні графіки були оновлені.")

                schedule_text = _format_schedule_text(data, include_building=False)
                schedule_body = "\n".join(schedule_text.splitlines()[1:]).strip()
                if schedule_body:
                    header_lines.append("")
                    header_lines.append(schedule_body)

                text = "\n".join(header_lines).strip()
                subscribers = await get_subscribers_for_schedule_notification(current_hour, building_id)
                existing_notifications = {
                    notif["chat_id"]: notif
                    for notif in await get_active_notifications("schedule")
                }
                logger.info(
                    "yasno: schedule notify building=%s subscribers=%s",
                    building_id,
                    len(subscribers),
                )

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🏠 Головне меню", callback_data="menu")]]
                )

                async def send_schedule(chat_id: int):
                    last_menu_id = await get_last_bot_message(chat_id)
                    if last_menu_id:
                        try:
                            await bot.delete_message(chat_id, last_menu_id)
                        except Exception:
                            pass
                        await delete_last_bot_message_record(chat_id)

                    prev = existing_notifications.get(chat_id)
                    if prev:
                        try:
                            await bot.delete_message(chat_id, prev["message_id"])
                        except Exception:
                            pass
                        await delete_notification(prev["id"])

                    msg = await bot.send_message(chat_id, text, reply_markup=keyboard)
                    await save_notification(chat_id, msg.message_id, "schedule")

                await broadcast_messages(subscribers, send_schedule)
        except Exception:
            logger.exception("yasno_schedule_monitor_loop error")

        await asyncio.sleep(_PLANNED_OUTAGES_TTL)
