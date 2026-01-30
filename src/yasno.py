import asyncio
import json
import logging
import os
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


def _now_ts() -> float:
    return datetime.now().timestamp()


async def _fetch_json(url: str, params: dict, timeout: int = 10):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Yasno API returned {resp.status}")
            return await resp.json()


def _parse_cached(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _building_env_prefix(building_id: int) -> str:
    return f"YASNO_BUILDING_{building_id}"


def _parse_queries(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip().strip('"').strip("'")
    if not value:
        return []
    parts = []
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


async def get_planned_outages(force: bool = False) -> dict | None:
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


def _format_slots(slots: list[dict]) -> str:
    ranges = []
    for start, end in _extract_definite_ranges(slots):
        ranges.append((start, end))
    if not ranges:
        return "немає відключень"

    def fmt(minutes: int) -> str:
        h = minutes // 60
        m = minutes % 60
        return f"{h:02d}:{m:02d}"

    return ", ".join(f"{fmt(s)}–{fmt(e)}" for s, e in ranges)


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
    if outage.get("status") != "ScheduleApplies":
        return date_label, "немає даних"
    slots = outage.get("slots", [])
    return date_label, _format_slots(slots)


async def _get_building_schedule_data(building_id: int) -> tuple[dict[str, Any] | None, str | None]:
    street_query, house_queries = _get_building_queries(building_id)
    if not street_query or not house_queries:
        return None, "ℹ️ Графіки для цього будинку не налаштовані."

    planned = await get_planned_outages()
    if not planned:
        return None, "⚠️ Дані про графіки від ЯСНО недоступні."

    street_id = await _get_street_id(street_query)
    if not street_id:
        return None, "⚠️ Не вдалося отримати адресу для графіків."

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
            queues.append({
                "key": group_key,
                "label": house.get("value") or house_query,
                "data": planned.get(group_key),
            })

    if not queues:
        return None, "⚠️ Графіки для цього будинку недоступні."

    building = get_building_by_id(building_id)
    return {"building": building, "queues": queues}, None


async def get_building_schedule_text(building_id: int) -> str:
    data, error = await _get_building_schedule_data(building_id)
    if error:
        return error

    queues = data["queues"]

    lines = ["🗓 <b>Орієнтовні графіки</b>"]
    building = data["building"]
    if building:
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


def _build_schedule_svg(data: dict[str, Any]) -> bytes:
    queues = data["queues"]
    building = data.get("building")

    label_width = 200
    hour_width = 18
    hours = 24
    grid_width = hour_width * hours
    header_height = 34
    row_height = 26
    row_gap = 6
    total_rows = len(queues) * 2
    height = header_height + total_rows * (row_height + row_gap) + 20
    width = label_width + grid_width + 20

    bg = "#f8f3ea"
    grid = "#d9cfc3"
    outage = "#c45b5b"
    text_main = "#3b2f2f"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{bg}" rx="18"/>',
        f'<text x="20" y="26" font-family="Arial, sans-serif" font-size="16" fill="{text_main}">Орієнтовні графіки</text>',
    ]

    if building:
        parts.append(
            f'<text x="20" y="46" font-family="Arial, sans-serif" font-size="12" fill="{text_main}">{building["name"]} ({building["address"]})</text>'
        )

    # Header hours
    base_x = 10 + label_width
    base_y = header_height
    parts.append(f'<rect x="{base_x}" y="{base_y - 24}" width="{grid_width}" height="24" fill="none" />')
    for h in range(hours):
        x = base_x + h * hour_width
        parts.append(f'<text x="{x + 2}" y="{base_y - 8}" font-family="Arial, sans-serif" font-size="9" fill="{text_main}">{h:02d}</text>')
        parts.append(f'<line x1="{x}" y1="{base_y - 6}" x2="{x}" y2="{height - 10}" stroke="{grid}" stroke-width="0.5"/>')

    y = base_y
    for queue in queues:
        queue_label = f'{queue["label"]} • {queue["key"]}'
        for day_key, day_label in (("today", "Сьогодні"), ("tomorrow", "Завтра")):
            day_data = queue["data"].get(day_key) if queue["data"] else None
            slots = day_data.get("slots", []) if day_data else []
            ranges = _extract_definite_ranges(slots)

            parts.append(f'<text x="20" y="{y + 17}" font-family="Arial, sans-serif" font-size="11" fill="{text_main}">{queue_label} — {day_label}</text>')
            parts.append(f'<rect x="{base_x}" y="{y}" width="{grid_width}" height="{row_height}" fill="white" rx="6" stroke="{grid}" stroke-width="0.6"/>')

            for start, end in ranges:
                x = base_x + (start / 60) * hour_width
                w = max(1, (end - start) / 60 * hour_width)
                parts.append(f'<rect x="{x:.1f}" y="{y + 2}" width="{w:.1f}" height="{row_height - 4}" fill="{outage}" rx="4"/>')

            y += row_height + row_gap

    parts.append("</svg>")
    return "\n".join(parts).encode("utf-8")


async def get_building_schedule_svg(building_id: int) -> tuple[bytes | None, str | None]:
    data, error = await _get_building_schedule_data(building_id)
    if error:
        return None, error
    svg = _build_schedule_svg(data)
    return svg, None


async def planned_outages_loop() -> None:
    while True:
        try:
            await get_planned_outages(force=True)
        except Exception:
            logger.exception("planned_outages_loop error")
        await asyncio.sleep(_PLANNED_OUTAGES_TTL)
