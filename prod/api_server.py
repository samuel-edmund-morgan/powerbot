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

import logging
from datetime import datetime
from aiohttp import web

from config import CFG
from database import (
    get_sensor_by_uuid,
    register_sensor,
    update_sensor_heartbeat,
    get_building_by_id,
)


logger = logging.getLogger(__name__)


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


def create_api_app() -> web.Application:
    """Створити aiohttp додаток для API сервера."""
    app = web.Application()
    
    # Додаємо маршрути
    app.router.add_post("/api/v1/heartbeat", heartbeat_handler)
    app.router.add_get("/api/v1/health", health_handler)
    app.router.add_get("/api/v1/sensors", sensors_info_handler)
    
    # Простий health check на корені
    app.router.add_get("/", health_handler)
    
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
