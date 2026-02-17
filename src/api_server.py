"""
API Server для отримання heartbeat від ESP32 сенсорів.

Endpoint: POST /api/v1/heartbeat
Body: {
    "api_key": "your-secret-key",
    "building_id": 1,
    "section_id": 2,
    "comment": "кв 123 (опц.)",
    "sensor_uuid": "esp32-newcastle-01"
}

Response: {"status": "ok", "timestamp": "2026-01-22T12:00:00Z"}
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from business import get_business_service, is_business_feature_enabled
from config import CFG
from yasno import get_planned_outages, get_building_schedule_text
from database import (
    get_sensor_by_uuid,
    get_active_sensor_by_public_id,
    get_all_active_sensors_with_public_ids,
    upsert_sensor_heartbeat,
    get_building_by_id,
    add_subscriber,
    get_subscriber_building_and_section,
    set_subscriber_building,
    set_subscriber_section,
    get_all_buildings,
    get_building_info,
    get_notification_settings,
    set_light_notifications,
    set_alert_notifications,
    set_schedule_notifications,
    set_quiet_hours,
    get_heating_stats,
    get_water_stats,
    get_user_vote,
    vote_heating,
    vote_water,
    get_all_general_services,
    get_all_places_with_likes,
    get_places_by_service_with_likes,
    tokenize_query,
    has_liked_place,
    like_place,
    unlike_place,
    get_shelter_places_with_likes,
    has_liked_shelter,
    like_shelter,
    unlike_shelter,
    get_sensors_by_building,
    get_last_event,
    db_get,
    db_set,
    default_section_for_building,
    get_building_section_count,
    is_valid_section_for_building,
)


logger = logging.getLogger(__name__)
webapp_logger = logging.getLogger("handlers")

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
MAPS_DIR = Path(__file__).resolve().parent / "maps"


def _extract_api_key_from_request(request: web.Request) -> str:
    """Extract API key from X-API-Key header, Bearer auth, or query param."""
    header_key = str(request.headers.get("X-API-Key") or "").strip()
    if header_key:
        return header_key

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer

    query_key = str(request.query.get("api_key") or "").strip()
    if query_key:
        return query_key

    return ""


def _sensor_is_online_by_heartbeat_only(sensor: dict) -> tuple[bool, int | None]:
    """Return online status using only last_heartbeat and timeout (ignores freeze)."""
    last_heartbeat = sensor.get("last_heartbeat")
    if not last_heartbeat:
        return False, None
    age_seconds = max(0, int((datetime.now() - last_heartbeat).total_seconds()))
    return age_seconds < int(CFG.sensor_timeout), age_seconds


def _filter_places_by_query(
    places: list[dict],
    query: str,
    limit: int = 20,
    *,
    verified_first: bool = False,
) -> list[dict]:
    tokens_raw = tokenize_query(query)
    if not tokens_raw:
        return []
    tokens: list[str] = []
    for t in tokens_raw:
        if t not in tokens:
            tokens.append(t)

    results: list[dict] = []
    for place in places:
        haystack = " ".join(
            [
                place.get("name") or "",
                place.get("description") or "",
                place.get("address") or "",
                place.get("keywords") or "",
            ]
        ).casefold()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            item = dict(place)
            item["_match_score"] = score
            results.append(item)

    def _tier_rank(value: str | None) -> int:
        tier = (value or "").strip().lower()
        return {"partner": 0, "pro": 1, "light": 2}.get(tier, 3)

    results.sort(
        key=lambda item: (
            -item.get("_match_score", 0),
            (0 if item.get("is_verified") else 1) if verified_first else 0,
            _tier_rank(item.get("verified_tier")) if verified_first else 0,
            -(item.get("likes_count") or 0),
            item.get("name") or "",
        )
    )
    trimmed = results[:limit]
    for item in trimmed:
        item.pop("_match_score", None)
    return trimmed


async def heartbeat_handler(request: web.Request) -> web.Response:
    """
    Обробник heartbeat запитів від ESP32 сенсорів.
    
    Очікує JSON:
    {
        "api_key": "secret-key",
        "building_id": 1,
        "section_id": 2,
        "sensor_uuid": "unique-sensor-id"
    }
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"status": "error", "message": "Invalid JSON"},
            status=400
        )
    
    # Валідація API ключа
    api_key = data.get("api_key")
    if not api_key or api_key != CFG.sensor_api_key:
        logger.warning(f"Invalid API key attempt: {api_key[:10] if api_key else 'None'}...")
        return web.json_response(
            {"status": "error", "message": "Invalid API key"},
            status=401
        )
    
    # Валідація building_id з payload (може бути нормалізований пізніше по uuid).
    building_id = data.get("building_id")
    if not isinstance(building_id, int):
        return web.json_response(
            {"status": "error", "message": "building_id must be an integer"},
            status=400
        )

    # Валідація sensor_uuid
    sensor_uuid = data.get("sensor_uuid")
    if not sensor_uuid or not isinstance(sensor_uuid, str):
        return web.json_response(
            {"status": "error", "message": "sensor_uuid is required and must be a string"},
            status=400
        )
    sensor_uuid = sensor_uuid.strip()
    sensor_uuid_key = sensor_uuid.lower()

    # Канонічне зіставлення uuid -> building_id.
    # Це захищає від розбіжності "прошивочного ID" vs канонічного ID будинку в БД.
    canonical_building_id = CFG.sensor_uuid_building_map.get(sensor_uuid_key)
    if canonical_building_id is not None and canonical_building_id != building_id:
        logger.warning(
            "Sensor %s reported building_id=%s; canonical mapping applied -> %s",
            sensor_uuid,
            building_id,
            canonical_building_id,
        )
        building_id = canonical_building_id

    # Перевіряємо що будинок існує (вже після canonical mapping).
    building = get_building_by_id(building_id)
    if not building:
        return web.json_response(
            {"status": "error", "message": f"Building {building_id} not found"},
            status=404
        )

    # Валідація section_id (1..N). Для backward-compat дозволяємо відсутність (ставимо дефолт).
    section_id = data.get("section_id")
    if section_id is None:
        section_id = default_section_for_building(building_id)
        logger.warning(
            "Heartbeat missing section_id: uuid=%s building=%s; defaulting section_id=%s",
            sensor_uuid,
            building_id,
            section_id,
        )
    max_sections = get_building_section_count(building_id)
    if not isinstance(section_id, int) or not is_valid_section_for_building(building_id, section_id):
        # Для відомих сенсорів з canonical mapping не роняємо heartbeat:
        # якщо секція з payload невалідна для канонічного будинку — ставимо дефолтну секцію.
        if sensor_uuid_key in CFG.sensor_uuid_building_map:
            fallback_section_id = default_section_for_building(building_id)
            logger.warning(
                "Sensor %s reported invalid section_id=%s for building_id=%s; defaulting section_id=%s",
                sensor_uuid,
                section_id,
                building_id,
                fallback_section_id,
            )
            section_id = fallback_section_id
            max_sections = get_building_section_count(building_id)
            if not isinstance(section_id, int) or not is_valid_section_for_building(building_id, section_id):
                return web.json_response(
                    {"status": "error", "message": f"section_id must be integer 1..{max_sections}"},
                    status=400,
                )
        else:
            return web.json_response(
                {"status": "error", "message": f"section_id must be integer 1..{max_sections}"},
                status=400,
            )

    sensor_name = data.get("name")
    if sensor_name is not None and not isinstance(sensor_name, str):
        return web.json_response(
            {"status": "error", "message": "name must be string"},
            status=400,
        )
    if isinstance(sensor_name, str):
        sensor_name = sensor_name.strip() or None

    comment = data.get("comment")
    if comment is not None and not isinstance(comment, str):
        return web.json_response(
            {"status": "error", "message": "comment must be string"},
            status=400,
        )
    if isinstance(comment, str):
        comment = comment.strip()
        if not comment:
            comment = None
        elif len(comment) > 160:
            comment = comment[:160]
    
    # Upsert сенсора + heartbeat (1 операція БД)
    sensor_before = await get_sensor_by_uuid(sensor_uuid)
    is_new = await upsert_sensor_heartbeat(sensor_uuid, building_id, section_id, sensor_name, comment)
    if is_new:
        logger.info(
            "New sensor registered: %s building=%s section=%s (%s)",
            sensor_uuid,
            building_id,
            section_id,
            building["name"],
        )
    else:
        if sensor_before and (
            sensor_before.get("building_id") != building_id
            or sensor_before.get("section_id") != section_id
        ):
            logger.warning(
                "Sensor %s moved: (%s,%s) -> (%s,%s)",
                sensor_uuid,
                sensor_before.get("building_id"),
                sensor_before.get("section_id"),
                building_id,
                section_id,
            )
    
    now = datetime.now().isoformat()
    
    return web.json_response({
        "status": "ok",
        "timestamp": now,
        "building": building["name"],
        "section_id": section_id,
        "sensor_uuid": sensor_uuid,
    })


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "powerbot-api",
    })


async def sensors_info_handler(request: web.Request) -> web.Response:
    """
    Інформація про сенсори (для адмінів).
    Потребує API ключа в заголовку.
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != CFG.sensor_api_key:
        return web.json_response(
            {"status": "error", "message": "Unauthorized"},
            status=401
        )
    
    from database import get_all_active_sensors
    
    sensors = await get_all_active_sensors()
    
    return web.json_response({
        "status": "ok",
        "sensors": [
            {
                "uuid": s["uuid"],
                "building_id": s["building_id"],
                "section_id": s.get("section_id"),
                "name": s["name"],
                "comment": s.get("comment"),
                "last_heartbeat": s["last_heartbeat"].isoformat() if s["last_heartbeat"] else None,
            }
            for s in sensors
        ],
        "total": len(sensors),
    })


def _validate_public_sensor_api_key(request: web.Request) -> tuple[bool, web.Response | None]:
    """Validate read-only public sensor API key."""
    configured_key = str(CFG.sensor_public_api_key or "").strip()
    if not configured_key:
        return False, web.json_response(
            {"status": "error", "message": "Public sensor API key is not configured"},
            status=503,
        )

    api_key = _extract_api_key_from_request(request)
    if not api_key or api_key != configured_key:
        return False, web.json_response(
            {"status": "error", "message": "Unauthorized"},
            status=401,
        )

    return True, None


async def public_sensors_status_handler(request: web.Request) -> web.Response:
    """Read-only status of all active sensors (freeze-independent)."""
    ok, error = _validate_public_sensor_api_key(request)
    if not ok:
        return error

    sensors = await get_all_active_sensors_with_public_ids()
    payload = []
    for sensor in sensors:
        is_up, _age_seconds = _sensor_is_online_by_heartbeat_only(sensor)
        sensor_id = sensor.get("public_id")
        if sensor_id is None:
            continue
        payload.append(
            {
                "id": int(sensor_id),
                "is_up": bool(is_up),
            }
        )

    return web.json_response({"sensors": payload})


async def public_sensor_status_handler(request: web.Request) -> web.Response:
    """Read-only status of a single sensor by numeric ID (freeze-independent)."""
    ok, error = _validate_public_sensor_api_key(request)
    if not ok:
        return error

    sensor_id_raw = str(request.match_info.get("sensor_id") or "").strip()
    if not sensor_id_raw:
        return web.json_response(
            {"status": "error", "message": "sensor_id is required"},
            status=400,
        )
    try:
        sensor_id = int(sensor_id_raw)
    except ValueError:
        return web.json_response(
            {"status": "error", "message": "sensor_id must be a positive integer"},
            status=400,
        )
    if sensor_id <= 0:
        return web.json_response(
            {"status": "error", "message": "sensor_id must be a positive integer"},
            status=400,
        )

    sensor = await get_active_sensor_by_public_id(sensor_id)
    if not sensor:
        return web.json_response(
            {"status": "error", "message": "Sensor not found"},
            status=404,
        )

    is_up, _age_seconds = _sensor_is_online_by_heartbeat_only(sensor)
    return web.json_response({"id": int(sensor["public_id"]), "is_up": bool(is_up)})


async def yasno_outages_handler(request: web.Request) -> web.Response:
    """Повернути кешовані графіки відключень ЯСНО."""
    if not CFG.yasno_enabled:
        return web.json_response({"status": "error", "message": "Disabled"}, status=503)
    data = await get_planned_outages()
    if not data:
        return web.json_response({"status": "error", "message": "No data"}, status=503)
    return web.json_response({"status": "ok", "data": data})


def _parse_init_data(init_data: str) -> dict | None:
    """Перевірити та розпарсити Telegram WebApp initData."""
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    provided_hash = data.pop("hash", None)
    if not provided_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", CFG.token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, provided_hash):
        return None

    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except json.JSONDecodeError:
            return None

    return data


def _get_webapp_user(request: web.Request) -> dict | None:
    """Отримати користувача WebApp з initData або debug-режиму."""
    if CFG.web_app_debug_user_id:
        return {"id": CFG.web_app_debug_user_id, "username": "debug", "first_name": "Debug"}

    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData")
    if not init_data:
        return None

    parsed = _parse_init_data(init_data)
    if not parsed:
        return None

    user = parsed.get("user")
    if not isinstance(user, dict) or "id" not in user:
        return None

    return user


def _format_webapp_user_label(user: dict | None) -> str:
    """Readable user label for logs: @username (First Last) - id."""
    if not user:
        return "unknown"
    user_id = user.get("id")
    username = user.get("username")
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    name = " ".join(part for part in [first, last] if part).strip()

    if username and name:
        return f"@{username} ({name}) - {user_id}"
    if username:
        return f"@{username} - {user_id}"
    if name:
        return f"{name} - {user_id}"
    return str(user_id) if user_id is not None else "unknown"


async def _ensure_subscriber(user: dict) -> None:
    """Переконатися, що користувач є у subscribers."""
    await add_subscriber(
        chat_id=int(user["id"]),
        username=user.get("username"),
        first_name=user.get("first_name"),
    )


def _serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _get_power_payload(building_id: int | None, section_id: int | None) -> dict:
    if not building_id or not is_valid_section_for_building(building_id, section_id):
        return {
            "building": None,
            "section_id": None,
            "is_up": None,
            "sensors_online": 0,
            "sensors_total": 0,
            "last_change": None,
            "last_event_type": None,
        }

    building = await get_building_info(building_id)
    sensors = await get_sensors_by_building(building_id)
    section_sensors = []
    for s in sensors:
        sid = s.get("section_id")
        if sid is None:
            sid = default_section_for_building(building_id)
        if sid == section_id:
            section_sensors.append(s)

    sensors_total = len(section_sensors)
    sensors_online = 0
    now = datetime.now()
    timeout = CFG.sensor_timeout
    for s in section_sensors:
        if s["last_heartbeat"]:
            delta = (now - s["last_heartbeat"]).total_seconds()
            if delta < timeout:
                sensors_online += 1

    is_up = None
    if sensors_total > 0:
        is_up = sensors_online > 0

    last_event = await get_last_event(building_id=building_id, section_id=section_id)
    last_change = None
    last_event_type = None
    if last_event:
        last_event_type, last_change = last_event

    return {
        "building": {
            "id": building["id"],
            "name": building["name"],
            "address": building["address"],
            "has_sensor": building["has_sensor"],
            "sensor_count": building["sensor_count"],
        } if building else None,
        "section_id": section_id,
        "is_up": is_up,
        "sensors_online": sensors_online,
        "sensors_total": sensors_total,
        "last_change": _serialize_dt(last_change),
        "last_event_type": last_event_type,
    }


def _strip_schedule_header(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("🗓"):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


async def _get_schedule_payload(building_id: int | None, section_id: int | None) -> dict:
    if not building_id or not is_valid_section_for_building(building_id, section_id):
        return {"text": ""}
    try:
        text = await get_building_schedule_text(
            building_id,
            section_id=section_id,
            include_building=False,
        )
    except Exception as exc:
        logger.warning("Failed to get webapp schedule: %s", exc)
        return {"text": "⚠️ Не вдалося отримати графіки."}

    if text:
        text = _strip_schedule_header(text)
    return {"text": text or ""}


async def _get_alert_payload() -> dict:
    state = await db_get("last_alert_state")
    if state not in {"active", "inactive"}:
        from alerts import check_alert_status
        current = await check_alert_status()
        if current is None:
            return {"status": "unknown"}
        state = "active" if current else "inactive"
        await db_set("last_alert_state", state)
    return {"status": state}


def _shelter_map_image(shelter: dict) -> str | None:
    mapping = {
        "Паркінг": "parking.png",
        "Комора": "komora.png",
    }
    key = shelter.get("address") or shelter.get("name", "")
    for name, filename in mapping.items():
        if name in key:
            return f"/maps/{filename}"
    return None


async def _get_shelters_payload(user_id: int) -> list[dict]:
    shelters = await get_shelter_places_with_likes()
    payload = []
    for s in shelters:
        liked = await has_liked_shelter(s["id"], user_id)
        payload.append({
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "address": s["address"],
            "likes_count": s["likes_count"],
            "liked": bool(liked),
            "map_image": _shelter_map_image(s),
        })
    return payload


async def webapp_bootstrap_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    webapp_logger.info("User %s webapp: bootstrap", _format_webapp_user_label(user))
    user_id = int(user["id"])

    building_id, section_id = await get_subscriber_building_and_section(user_id)
    buildings = await get_all_buildings()
    notifications = await get_notification_settings(user_id)
    power = await _get_power_payload(building_id, section_id)
    schedule = await _get_schedule_payload(building_id, section_id)
    alerts_payload = await _get_alert_payload()
    heating_stats = await get_heating_stats(building_id, section_id)
    water_stats = await get_water_stats(building_id, section_id)
    heating_vote = await get_user_vote(user_id, "heating", building_id, section_id)
    water_vote = await get_user_vote(user_id, "water", building_id, section_id)
    shelters = await _get_shelters_payload(user_id)
    categories = await get_all_general_services()

    return web.json_response({
        "status": "ok",
        "user": {
            "id": user_id,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        },
        "settings": {
            **notifications,
            "building_id": building_id,
            "section_id": section_id,
        },
        "buildings": buildings,
        "power": power,
        "schedule": schedule,
        "alerts": alerts_payload,
        "heating": {**heating_stats, "user_vote": heating_vote},
        "water": {**water_stats, "user_vote": water_vote},
        "shelters": shelters,
        "categories": categories,
        "services": {
            "security_phone": CFG.security_phone,
            "plumber_phone": CFG.plumber_phone,
            "electrician_phone": CFG.electrician_phone,
            "elevator_phones": CFG.elevator_phones,
        },
    })


async def webapp_status_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    webapp_logger.info("User %s webapp: status", _format_webapp_user_label(user))
    user_id = int(user["id"])
    building_id, section_id = await get_subscriber_building_and_section(user_id)

    power = await _get_power_payload(building_id, section_id)
    schedule = await _get_schedule_payload(building_id, section_id)
    alerts_payload = await _get_alert_payload()
    heating_stats = await get_heating_stats(building_id, section_id)
    water_stats = await get_water_stats(building_id, section_id)
    heating_vote = await get_user_vote(user_id, "heating", building_id, section_id)
    water_vote = await get_user_vote(user_id, "water", building_id, section_id)

    return web.json_response({
        "status": "ok",
        "power": power,
        "schedule": schedule,
        "alerts": alerts_payload,
        "heating": {**heating_stats, "user_vote": heating_vote},
        "water": {**water_stats, "user_vote": water_vote},
    })


async def webapp_building_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    building_id = data.get("building_id")
    if not isinstance(building_id, int):
        return web.json_response({"status": "error", "message": "building_id must be integer"}, status=400)

    building = await get_building_info(building_id)
    if not building:
        return web.json_response({"status": "error", "message": "Building not found"}, status=404)

    section_id = data.get("section_id")
    if section_id is None:
        section_id = default_section_for_building(building_id)
    max_sections = get_building_section_count(building_id)
    if not isinstance(section_id, int) or not is_valid_section_for_building(building_id, section_id):
        return web.json_response({"status": "error", "message": f"section_id must be integer 1..{max_sections}"}, status=400)

    user_id = int(user["id"])
    updated_building = await set_subscriber_building(user_id, building_id)
    updated_section = await set_subscriber_section(user_id, section_id)
    webapp_logger.info(
        "User %s webapp: set building %s section %s",
        _format_webapp_user_label(user),
        building_id,
        section_id,
    )
    return web.json_response(
        {
            "status": "ok",
            "updated": bool(updated_building or updated_section),
            "building": building,
            "section_id": section_id,
        }
    )


async def webapp_notifications_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    user_id = int(user["id"])
    if "light_notifications" in data:
        await set_light_notifications(user_id, bool(data["light_notifications"]))
    if "alert_notifications" in data:
        await set_alert_notifications(user_id, bool(data["alert_notifications"]))
    if "schedule_notifications" in data:
        await set_schedule_notifications(user_id, bool(data["schedule_notifications"]))

    quiet_start = data.get("quiet_start")
    quiet_end = data.get("quiet_end")
    if quiet_start is not None or quiet_end is not None:
        if quiet_start is not None and not isinstance(quiet_start, int):
            return web.json_response({"status": "error", "message": "quiet_start must be integer"}, status=400)
        if quiet_end is not None and not isinstance(quiet_end, int):
            return web.json_response({"status": "error", "message": "quiet_end must be integer"}, status=400)
        await set_quiet_hours(user_id, quiet_start, quiet_end)

    webapp_logger.info("User %s webapp: update notifications", _format_webapp_user_label(user))
    settings = await get_notification_settings(user_id)
    return web.json_response({"status": "ok", "settings": settings})


async def webapp_vote_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    vote_type = data.get("type")
    value = data.get("value")
    if vote_type not in {"heating", "water"} or not isinstance(value, bool):
        return web.json_response({"status": "error", "message": "Invalid vote payload"}, status=400)

    user_id = int(user["id"])
    building_id, section_id = await get_subscriber_building_and_section(user_id)
    if not building_id or not is_valid_section_for_building(building_id, section_id):
        return web.json_response({"status": "error", "message": "Select building and section first"}, status=400)

    if vote_type == "heating":
        await vote_heating(user_id, value, building_id, section_id)
    else:
        await vote_water(user_id, value, building_id, section_id)

    heating_stats = await get_heating_stats(building_id, section_id)
    water_stats = await get_water_stats(building_id, section_id)
    heating_vote = await get_user_vote(user_id, "heating", building_id, section_id)
    water_vote = await get_user_vote(user_id, "water", building_id, section_id)

    webapp_logger.info(
        "User %s webapp: vote %s=%s",
        _format_webapp_user_label(user),
        vote_type,
        value,
    )
    return web.json_response({
        "status": "ok",
        "heating": {**heating_stats, "user_vote": heating_vote},
        "water": {**water_stats, "user_vote": water_vote},
    })


async def webapp_places_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    webapp_logger.info("User %s webapp: places", _format_webapp_user_label(user))
    user_id = int(user["id"])

    query = (request.query.get("q") or "").strip()
    service_id = request.query.get("service_id")
    places: list[dict] = []
    if query:
        if service_id is not None:
            try:
                service_id_int = int(service_id)
            except ValueError:
                return web.json_response({"status": "error", "message": "Invalid service_id"}, status=400)
            base_places = await get_places_by_service_with_likes(service_id_int)
        else:
            base_places = await get_all_places_with_likes()
        if is_business_feature_enabled():
            base_places = await get_business_service().enrich_places_for_main_bot(base_places)
        places = _filter_places_by_query(base_places, query, verified_first=is_business_feature_enabled())
    elif service_id is not None:
        try:
            service_id_int = int(service_id)
        except ValueError:
            return web.json_response({"status": "error", "message": "Invalid service_id"}, status=400)
        places = await get_places_by_service_with_likes(service_id_int)
    else:
        places = await get_all_places_with_likes()

    if not query:
        if is_business_feature_enabled():
            places = await get_business_service().enrich_places_for_main_bot(places)

            def _tier_rank(value: str | None) -> int:
                tier = (value or "").strip().lower()
                return {"partner": 0, "pro": 1, "light": 2}.get(tier, 3)

            places.sort(
                key=lambda item: (
                    0 if item.get("is_verified") else 1,
                    _tier_rank(item.get("verified_tier")),
                    -(item.get("likes_count") or 0),
                    item.get("name") or "",
                )
            )

    payload = []
    for p in places:
        liked = await has_liked_place(p["id"], user_id)
        payload.append({**p, "liked": bool(liked)})

    return web.json_response({"status": "ok", "places": payload})


async def webapp_place_like_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    user_id = int(user["id"])
    place_id = int(request.match_info.get("place_id"))
    await like_place(place_id, user_id)
    webapp_logger.info(
        "User %s webapp: like place %s",
        _format_webapp_user_label(user),
        place_id,
    )
    return web.json_response({"status": "ok"})


async def webapp_place_unlike_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    user_id = int(user["id"])
    place_id = int(request.match_info.get("place_id"))
    await unlike_place(place_id, user_id)
    webapp_logger.info(
        "User %s webapp: unlike place %s",
        _format_webapp_user_label(user),
        place_id,
    )
    return web.json_response({"status": "ok"})


async def webapp_shelters_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    webapp_logger.info("User %s webapp: shelters", _format_webapp_user_label(user))
    user_id = int(user["id"])
    shelters = await _get_shelters_payload(user_id)
    return web.json_response({"status": "ok", "shelters": shelters})


async def webapp_shelter_like_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    user_id = int(user["id"])
    shelter_id = int(request.match_info.get("shelter_id"))
    await like_shelter(shelter_id, user_id)
    webapp_logger.info(
        "User %s webapp: like shelter %s",
        _format_webapp_user_label(user),
        shelter_id,
    )
    return web.json_response({"status": "ok"})


async def webapp_shelter_unlike_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    user_id = int(user["id"])
    shelter_id = int(request.match_info.get("shelter_id"))
    await unlike_shelter(shelter_id, user_id)
    webapp_logger.info(
        "User %s webapp: unlike shelter %s",
        _format_webapp_user_label(user),
        shelter_id,
    )
    return web.json_response({"status": "ok"})


async def webapp_index_handler(_: web.Request) -> web.Response:
    """Повертає HTML WebApp."""
    if not WEBAPP_DIR.exists():
        return web.Response(status=404, text="WebApp not found")
    index_path = WEBAPP_DIR / "index.html"
    try:
        content = index_path.read_text(encoding="utf-8")
    except OSError:
        return web.Response(status=404, text="WebApp not found")
    version = str(int(time.time()))
    content = content.replace("__WEBAPP_VERSION__", version)
    return web.Response(
        text=content,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def create_api_app() -> web.Application:
    """Створити aiohttp додаток для API сервера."""
    app = web.Application()
    
    # Додаємо маршрути
    app.router.add_post("/api/v1/heartbeat", heartbeat_handler)
    app.router.add_get("/api/v1/health", health_handler)
    app.router.add_get("/api/v1/sensors", sensors_info_handler)
    app.router.add_get("/api/v1/public/sensors/status", public_sensors_status_handler)
    app.router.add_get("/api/v1/public/sensors/{sensor_id:\\d+}/status", public_sensor_status_handler)
    app.router.add_get("/api/v1/yasno/outages", yasno_outages_handler)

    # Web App API
    app.router.add_get("/api/v1/webapp/bootstrap", webapp_bootstrap_handler)
    app.router.add_get("/api/v1/webapp/status", webapp_status_handler)
    app.router.add_post("/api/v1/webapp/building", webapp_building_handler)
    app.router.add_post("/api/v1/webapp/notifications", webapp_notifications_handler)
    app.router.add_post("/api/v1/webapp/vote", webapp_vote_handler)
    app.router.add_get("/api/v1/webapp/places", webapp_places_handler)
    app.router.add_post("/api/v1/webapp/places/{place_id}/like", webapp_place_like_handler)
    app.router.add_post("/api/v1/webapp/places/{place_id}/unlike", webapp_place_unlike_handler)
    app.router.add_get("/api/v1/webapp/shelters", webapp_shelters_handler)
    app.router.add_post("/api/v1/webapp/shelters/{shelter_id}/like", webapp_shelter_like_handler)
    app.router.add_post("/api/v1/webapp/shelters/{shelter_id}/unlike", webapp_shelter_unlike_handler)
    
    # Простий health check на корені
    app.router.add_get("/", health_handler)

    # Web App статика
    app.router.add_get("/app", webapp_index_handler)
    app.router.add_get("/app/", webapp_index_handler)
    if WEBAPP_DIR.exists():
        app.router.add_static("/app/", WEBAPP_DIR, show_index=False)
    if MAPS_DIR.exists():
        app.router.add_static("/maps/", MAPS_DIR, show_index=False)
    
    return app


async def start_api_server(app: web.Application) -> web.AppRunner:
    """Запустити API сервер."""
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, "0.0.0.0", CFG.api_port)
    await site.start()
    
    logger.info(f"API server started on port {CFG.api_port}")
    
    return runner


async def stop_api_server(runner: web.AppRunner):
    """Зупинити API сервер."""
    await runner.cleanup()
    logger.info("API server stopped")
