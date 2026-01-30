"""
API Server для отримання heartbeat від ESP32 сенсорів.

Endpoint: POST /api/v1/heartbeat
Body: {
    "api_key": "your-secret-key",
    "building_id": 1,
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

from config import CFG
from database import (
    get_sensor_by_uuid,
    register_sensor,
    update_sensor_heartbeat,
    get_building_by_id,
    add_subscriber,
    get_subscriber_building,
    set_subscriber_building,
    get_all_buildings,
    get_building_info,
    get_notification_settings,
    set_light_notifications,
    set_alert_notifications,
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
)
from yasno import get_planned_outages


logger = logging.getLogger(__name__)
webapp_logger = logging.getLogger("handlers")

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
MAPS_DIR = Path(__file__).resolve().parent / "maps"
DONATE_URL = "https://send.monobank.ua/jar/7d56pmvjEB"


def _filter_places_by_query(places: list[dict], query: str, limit: int = 20) -> list[dict]:
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

    results.sort(
        key=lambda item: (
            -item.get("_match_score", 0),
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
    
    # Валідація building_id
    building_id = data.get("building_id")
    if not isinstance(building_id, int):
        return web.json_response(
            {"status": "error", "message": "building_id must be an integer"},
            status=400
        )
    
    # Перевіряємо що будинок існує
    building = get_building_by_id(building_id)
    if not building:
        return web.json_response(
            {"status": "error", "message": f"Building {building_id} not found"},
            status=404
        )
    
    # Валідація sensor_uuid
    sensor_uuid = data.get("sensor_uuid")
    if not sensor_uuid or not isinstance(sensor_uuid, str):
        return web.json_response(
            {"status": "error", "message": "sensor_uuid is required and must be a string"},
            status=400
        )
    
    # Перевіряємо/реєструємо сенсор
    sensor = await get_sensor_by_uuid(sensor_uuid)
    
    if sensor is None:
        # Новий сенсор — реєструємо
        sensor_name = data.get("name")  # Опціональна назва
        is_new = await register_sensor(sensor_uuid, building_id, sensor_name)
        if is_new:
            logger.info(f"New sensor registered: {sensor_uuid} for building {building_id} ({building['name']})")
    else:
        # Існуючий сенсор — перевіряємо building_id
        if sensor["building_id"] != building_id:
            logger.warning(
                f"Sensor {sensor_uuid} registered for building {sensor['building_id']}, "
                f"but heartbeat received for building {building_id}"
            )
            # Оновлюємо building_id якщо змінився
            await register_sensor(sensor_uuid, building_id)
    
    # Оновлюємо heartbeat
    await update_sensor_heartbeat(sensor_uuid)
    
    now = datetime.now().isoformat()
    
    return web.json_response({
        "status": "ok",
        "timestamp": now,
        "building": building["name"],
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
                "name": s["name"],
                "last_heartbeat": s["last_heartbeat"].isoformat() if s["last_heartbeat"] else None,
            }
            for s in sensors
        ],
        "total": len(sensors),
    })


async def yasno_outages_handler(request: web.Request) -> web.Response:
    """Повернути кешовані графіки відключень ЯСНО."""
    data = await get_planned_outages()
    if not data:
        return web.json_response(
            {"status": "error", "message": "No data"},
            status=503,
        )
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


async def _get_power_payload(building_id: int | None) -> dict:
    if not building_id:
        return {
            "building": None,
            "is_up": None,
            "sensors_online": 0,
            "sensors_total": 0,
            "last_change": None,
            "last_event_type": None,
        }

    building = await get_building_info(building_id)
    sensors = await get_sensors_by_building(building_id)
    sensors_total = len(sensors)
    sensors_online = 0
    now = datetime.now()
    timeout = CFG.sensor_timeout
    for s in sensors:
        if s["last_heartbeat"]:
            delta = (now - s["last_heartbeat"]).total_seconds()
            if delta < timeout:
                sensors_online += 1

    is_up = None
    if sensors_total > 0:
        is_up = sensors_online > 0

    last_event = await get_last_event()
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
        "is_up": is_up,
        "sensors_online": sensors_online,
        "sensors_total": sensors_total,
        "last_change": _serialize_dt(last_change),
        "last_event_type": last_event_type,
    }


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

    building_id = await get_subscriber_building(user_id)
    buildings = await get_all_buildings()
    notifications = await get_notification_settings(user_id)
    power = await _get_power_payload(building_id)
    alerts_payload = await _get_alert_payload()
    heating_stats = await get_heating_stats(building_id)
    water_stats = await get_water_stats(building_id)
    heating_vote = await get_user_vote(user_id, "heating")
    water_vote = await get_user_vote(user_id, "water")
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
        },
        "buildings": buildings,
        "power": power,
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
        "donate_url": DONATE_URL,
    })


async def webapp_status_handler(request: web.Request) -> web.Response:
    user = _get_webapp_user(request)
    if not user:
        return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

    await _ensure_subscriber(user)
    webapp_logger.info("User %s webapp: status", _format_webapp_user_label(user))
    user_id = int(user["id"])
    building_id = await get_subscriber_building(user_id)

    power = await _get_power_payload(building_id)
    alerts_payload = await _get_alert_payload()
    heating_stats = await get_heating_stats(building_id)
    water_stats = await get_water_stats(building_id)
    heating_vote = await get_user_vote(user_id, "heating")
    water_vote = await get_user_vote(user_id, "water")

    return web.json_response({
        "status": "ok",
        "power": power,
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

    success = await set_subscriber_building(int(user["id"]), building_id)
    webapp_logger.info(
        "User %s webapp: set building %s",
        _format_webapp_user_label(user),
        building_id,
    )
    return web.json_response({"status": "ok", "updated": success, "building": building})


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
    building_id = await get_subscriber_building(user_id)
    if not building_id:
        return web.json_response({"status": "error", "message": "Select building first"}, status=400)

    if vote_type == "heating":
        await vote_heating(user_id, value, building_id)
    else:
        await vote_water(user_id, value, building_id)

    heating_stats = await get_heating_stats(building_id)
    water_stats = await get_water_stats(building_id)
    heating_vote = await get_user_vote(user_id, "heating")
    water_vote = await get_user_vote(user_id, "water")

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
        places = _filter_places_by_query(base_places, query)
    elif service_id is not None:
        try:
            service_id_int = int(service_id)
        except ValueError:
            return web.json_response({"status": "error", "message": "Invalid service_id"}, status=400)
        places = await get_places_by_service_with_likes(service_id_int)
    else:
        places = await get_all_places_with_likes()

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
