import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from config import DB_PATH
from sqlite_lock_logger import log_sqlite_lock_event


# Список всіх будинків ЖК "Нова Англія"
BUILDINGS = [
    {"id": 1, "name": "Ньюкасл", "address": "24-в", "has_sensor": False},
    {"id": 2, "name": "Оксфорд", "address": "28-б", "has_sensor": False},
    {"id": 3, "name": "Кембрідж", "address": "26", "has_sensor": False},
    {"id": 4, "name": "Ліверпуль", "address": "24-а", "has_sensor": False},
    {"id": 5, "name": "Брістоль", "address": "24-б", "has_sensor": False},
    {"id": 6, "name": "Бермінгем", "address": "26-б", "has_sensor": False},
    {"id": 7, "name": "Честер", "address": "28-д", "has_sensor": False},
    {"id": 8, "name": "Манчестер", "address": "26-г", "has_sensor": False},
    {"id": 9, "name": "Брайтон", "address": "26-в", "has_sensor": False},
    {"id": 10, "name": "Лондон", "address": "28-е", "has_sensor": False},
    {"id": 11, "name": "Лінкольн", "address": "28-к", "has_sensor": False},
    {"id": 12, "name": "Віндзор", "address": "26-д", "has_sensor": False},
    {"id": 13, "name": "Ноттінгем", "address": "24-г", "has_sensor": False},
    {"id": 14, "name": "Престон", "address": "-", "has_sensor": False},
]

# Початкові дані для укриттів
SHELTER_PLACES = [
    {
        "id": 1,
        "name": "🚗 Паркінг",
        "description": "Підземний паркінг ЖК. Відносно безпечне місце під час тривоги.",
        "address": "Паркінг",
    },
    {
        "id": 2,
        "name": "📦 Комора",
        "description": "Комора для мешканців Кембріджа. Відносно безпечне місце під час тривоги.",
        "address": "Комора",
    },
]

# ID будинку Ньюкасл - для існуючих користувачів
NEWCASTLE_BUILDING_ID = 1
DEFAULT_SECTION_OTHER_BUILDINGS = 1
DEFAULT_SECTION_NEWCASTLE = 2
VALID_SECTION_IDS = {1, 2, 3}
# Деякі будинки мають лише 2 секції (а не 3).
TWO_SECTION_BUILDING_IDS = {2, 3, 4, 11, 12}  # Оксфорд, Кембрідж, Ліверпуль, Лінкольн, Віндзор
DEFAULT_BUILDING_SECTION_COUNT = 3
SQLITE_BUSY_TIMEOUT_MS = 5000
logger = logging.getLogger(__name__)


def get_building_section_count(building_id: int | None) -> int:
    """Return number of sections for a building (default: 3)."""
    if building_id is None:
        return DEFAULT_BUILDING_SECTION_COUNT
    try:
        bid = int(building_id)
    except Exception:
        return DEFAULT_BUILDING_SECTION_COUNT
    if bid in TWO_SECTION_BUILDING_IDS:
        return 2
    return DEFAULT_BUILDING_SECTION_COUNT


def get_building_section_ids(building_id: int | None) -> tuple[int, ...]:
    """Return valid section ids for a building, in order (1..N)."""
    count = get_building_section_count(building_id)
    if count <= 1:
        return (1,)
    return tuple(range(1, count + 1))


def is_valid_section_for_building(building_id: int | None, section_id: int | None) -> bool:
    """Validate section_id against building-specific section count."""
    if building_id is None or section_id is None:
        return False
    try:
        sid = int(section_id)
    except Exception:
        return False
    if sid <= 0:
        return False
    return sid <= get_building_section_count(building_id)


def default_section_for_building(building_id: int | None) -> int | None:
    """Default section for legacy users/sensors when section_id is missing."""
    if building_id is None:
        return None
    return DEFAULT_SECTION_NEWCASTLE if building_id == NEWCASTLE_BUILDING_ID else DEFAULT_SECTION_OTHER_BUILDINGS


async def apply_sqlite_pragmas(db: aiosqlite.Connection) -> None:
    """Apply SQLite settings for concurrent access from multiple bot processes."""
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")


@asynccontextmanager
async def open_db() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(DB_PATH) as db:
        await apply_sqlite_pragmas(db)
        # Unicode-safe casefold for LIKE-based search (SQLite LOWER/NOCASE is ASCII-centric).
        await db.create_function("UCFOLD", 1, lambda value: str(value or "").casefold())
        yield db


def get_building_display_name(building: dict) -> str:
    """Отримати відображуване ім'я будинку (наприклад: 'Ньюкасл (24-в)')."""
    return f"{building['name']} ({building['address']})"


def get_building_by_id(building_id: int) -> dict | None:
    """Отримати будинок за ID."""
    for b in BUILDINGS:
        if b["id"] == building_id:
            return b
    return None


def build_keywords(name: str | None, description: str | None, keywords: str | None = None) -> str:
    """Зібрати ключові слова: існуючі keywords + назва + опис."""
    parts = []
    for text in (keywords, name, description):
        if text:
            parts.append(text)
    merged = " ".join(parts)
    # Нормалізуємо пробіли й робимо нижній регістр для уніфікації пошуку
    merged = " ".join(merged.split()).strip().casefold()
    return merged


def tokenize_query(query: str) -> list[str]:
    """Отримати токени з пошукового запиту, прибравши розділові знаки."""
    tokens = re.findall(r"[\wа-яіїєґ’'-]+", query.casefold())
    cleaned = []
    stopwords = {"де", "в", "на", "і", "та", "a", "the", "is", "світло"}
    for token in tokens:
        t = token.strip("-'’")
        if not t or len(t) <= 3:
            continue
        if t in stopwords:
            continue
        cleaned.append(t)
    return cleaned


async def init_db():
    """Ініціалізація бази даних: створення таблиць."""
    async with open_db() as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                quiet_start INTEGER DEFAULT NULL,
                quiet_end INTEGER DEFAULT NULL
            )"""
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)"
        )
        # Черга адмін-задач (control-plane): виконується основним ботом у data-plane.
        await db.execute(
            """CREATE TABLE IF NOT EXISTS admin_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                created_by INTEGER DEFAULT NULL,
                started_at TEXT DEFAULT NULL,
                finished_at TEXT DEFAULT NULL,
                updated_at TEXT DEFAULT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                progress_current INTEGER DEFAULT 0,
                progress_total INTEGER DEFAULT 0,
                last_error TEXT DEFAULT NULL
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_jobs_status_created ON admin_jobs (status, created_at)"
        )
        # Таблиця історії подій (up/down)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                building_id INTEGER DEFAULT NULL,
                section_id INTEGER DEFAULT NULL
            )"""
        )
        # Міграція: додати колонки quiet_start/quiet_end якщо їх немає
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN quiet_start INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN quiet_end INTEGER DEFAULT NULL")
        except Exception:
            pass
        # Міграція: додати інформацію про користувача
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN username TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN first_name TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN subscribed_at TEXT DEFAULT NULL")
        except Exception:
            pass
        # Таблиця категорій послуг (кафе, аптеки, тощо)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS general_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )"""
        )
        # Таблиця закладів
        await db.execute(
            """CREATE TABLE IF NOT EXISTS places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                address TEXT,
                FOREIGN KEY (service_id) REFERENCES general_services(id) ON DELETE CASCADE
            )"""
        )
        # Таблиця укриттів (спрощений список місць)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS shelter_places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                address TEXT,
                keywords TEXT DEFAULT NULL
            )"""
        )
        # Таблиця голосування за стан опалення
        await db.execute(
            """CREATE TABLE IF NOT EXISTS heating_votes (
                chat_id INTEGER PRIMARY KEY,
                has_heating INTEGER NOT NULL,
                voted_at TEXT NOT NULL
            )"""
        )
        # Таблиця голосування за стан води
        await db.execute(
            """CREATE TABLE IF NOT EXISTS water_votes (
                chat_id INTEGER PRIMARY KEY,
                has_water INTEGER NOT NULL,
                voted_at TEXT NOT NULL
            )"""
        )
        # Таблиця активних сповіщень для оновлення статистики
        await db.execute(
            """CREATE TABLE IF NOT EXISTS active_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                notification_type TEXT DEFAULT 'power_change'
            )"""
        )
        # Кеш стану графіків ЯСНО (для відстеження змін)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS yasno_schedule_state (
                building_id INTEGER NOT NULL,
                queue_key TEXT NOT NULL,
                day_key TEXT NOT NULL,
                status TEXT DEFAULT NULL,
                slots_hash TEXT DEFAULT NULL,
                updated_at TEXT DEFAULT NULL,
                PRIMARY KEY (building_id, queue_key, day_key)
            )"""
        )
        # Таблиця останнього повідомлення бота для кожного чату (для чистого чату)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS last_bot_message (
                chat_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            )"""
        )
        # Міграція: додати колонку keywords до places
        try:
            await db.execute("ALTER TABLE places ADD COLUMN keywords TEXT DEFAULT NULL")
        except Exception:
            pass
        # Міграція: видимість закладів у resident-каталозі (legacy БД без publish-флага).
        try:
            await db.execute("ALTER TABLE places ADD COLUMN is_published INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        # Міграція: колонки бізнес-режиму для places
        try:
            await db.execute("ALTER TABLE places ADD COLUMN is_verified INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN verified_tier TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN verified_until TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN promo_slot_until TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN business_enabled INTEGER DEFAULT 0")
        except Exception:
            pass
        # Backfill legacy verified Pro rows: promo-slot follows verified window by default.
        try:
            await db.execute(
                """
                UPDATE places
                   SET promo_slot_until = verified_until
                 WHERE promo_slot_until IS NULL
                   AND COALESCE(is_verified, 0) = 1
                   AND lower(COALESCE(verified_tier, '')) = 'pro'
                   AND verified_until IS NOT NULL
                """
            )
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN opening_hours TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN contact_type TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN contact_value TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN link_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN logo_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN photo_1_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN photo_2_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN photo_3_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN promo_code TEXT DEFAULT NULL")
        except Exception:
            pass
        # Міграція: додаткові Premium-кнопки в картці закладу
        try:
            await db.execute("ALTER TABLE places ADD COLUMN menu_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN order_url TEXT DEFAULT NULL")
        except Exception:
            pass
        # Міграція: офери/акції для Premium+
        try:
            await db.execute("ALTER TABLE places ADD COLUMN offer_1_text TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN offer_2_text TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN offer_1_image_url TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE places ADD COLUMN offer_2_image_url TEXT DEFAULT NULL")
        except Exception:
            pass
        # Таблиця лайків закладів
        await db.execute(
            """CREATE TABLE IF NOT EXISTS place_likes (
                place_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                liked_at TEXT NOT NULL,
                PRIMARY KEY (place_id, chat_id),
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        # Перегляди карток закладів (агрегація по днях) для бізнес-статистики.
        await db.execute(
            """CREATE TABLE IF NOT EXISTS place_views_daily (
                place_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (place_id, day),
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_place_views_daily_day ON place_views_daily (day)")
        # Кліки по інтерактивним елементам картки закладу (агрегація по днях + action).
        await db.execute(
            """CREATE TABLE IF NOT EXISTS place_clicks_daily (
                place_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                action TEXT NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (place_id, day, action),
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_place_clicks_daily_day ON place_clicks_daily (day)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_place_clicks_daily_action ON place_clicks_daily (action)")
        # Галерея медіа закладу (0..N фото/зображень).
        await db.execute(
            """CREATE TABLE IF NOT EXISTS place_gallery_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                media_ref TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                created_by INTEGER DEFAULT NULL,
                UNIQUE (place_id, media_ref),
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_place_gallery_media_place_pos "
            "ON place_gallery_media (place_id, position, id)"
        )
        # Репорти мешканців про неточності в картках закладів (модерація в adminbot).
        await db.execute(
            """CREATE TABLE IF NOT EXISTS place_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                reporter_tg_user_id INTEGER NOT NULL,
                reporter_username TEXT DEFAULT NULL,
                reporter_first_name TEXT DEFAULT NULL,
                reporter_last_name TEXT DEFAULT NULL,
                report_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT DEFAULT NULL,
                resolved_by INTEGER DEFAULT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_place_reports_status_created ON place_reports (status, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_place_reports_place_id ON place_reports (place_id)")
        # Запити Partner-власників у пріоритетну підтримку (ops).
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_support_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                owner_tg_user_id INTEGER NOT NULL,
                owner_username TEXT DEFAULT NULL,
                owner_first_name TEXT DEFAULT NULL,
                owner_last_name TEXT DEFAULT NULL,
                message_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT DEFAULT NULL,
                resolved_by INTEGER DEFAULT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_support_requests_status_created "
            "ON business_support_requests (status, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_support_requests_place_id "
            "ON business_support_requests (place_id)"
        )
        # Таблиця лайків укриттів
        await db.execute(
            """CREATE TABLE IF NOT EXISTS shelter_likes (
                place_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                liked_at TEXT NOT NULL,
                PRIMARY KEY (place_id, chat_id),
                FOREIGN KEY (place_id) REFERENCES shelter_places(id) ON DELETE CASCADE
            )"""
        )
        # Міграція: додати колонки для налаштувань сповіщень
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN light_notifications INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN alert_notifications INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN schedule_notifications INTEGER DEFAULT 1")
        except Exception:
            pass
        
        # === НОВА МІГРАЦІЯ: Підтримка будинків ===
        # Таблиця будинків
        await db.execute(
            """CREATE TABLE IF NOT EXISTS buildings (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                has_sensor INTEGER DEFAULT 0,
                sensor_count INTEGER DEFAULT 0
            )"""
        )
        
        # Заповнюємо таблицю будинків якщо вона порожня
        async with db.execute("SELECT COUNT(*) FROM buildings") as cur:
            row = await cur.fetchone()
            if row[0] == 0:
                for b in BUILDINGS:
                    await db.execute(
                        "INSERT INTO buildings(id, name, address, has_sensor, sensor_count) VALUES(?, ?, ?, ?, ?)",
                        (b["id"], b["name"], b["address"], 1 if b["has_sensor"] else 0, 1 if b["has_sensor"] else 0)
                    )

        # Заповнюємо таблицю укриттів якщо вона порожня
        async with db.execute("SELECT COUNT(*) FROM shelter_places") as cur:
            row = await cur.fetchone()
            if row[0] == 0:
                for s in SHELTER_PLACES:
                    await db.execute(
                        "INSERT INTO shelter_places(id, name, description, address) VALUES(?, ?, ?, ?)",
                        (s["id"], s["name"], s["description"], s["address"])
                    )
        
        # Міграція: додати колонку building_id до subscribers
        # Для ІСНУЮЧИХ користувачів - Ньюкасл (id=1) за замовчуванням
        # Для НОВИХ користувачів - NULL (потрібно обрати будинок)
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN building_id INTEGER DEFAULT NULL")
            # Встановлюємо Ньюкасл для всіх існуючих користувачів
            await db.execute(
                "UPDATE subscribers SET building_id = ? WHERE building_id IS NULL",
                (NEWCASTLE_BUILDING_ID,)
            )
        except Exception:
            pass

        # Міграція: додати колонку section_id до subscribers
        # Для ІСНУЮЧИХ користувачів: Ньюкасл -> 2 секція, решта -> 1 секція.
        try:
            await db.execute("ALTER TABLE subscribers ADD COLUMN section_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute(
                """
                UPDATE subscribers
                   SET section_id = CASE
                       WHEN building_id = ? THEN ?
                       ELSE ?
                   END
                 WHERE section_id IS NULL
                   AND building_id IS NOT NULL
                """,
                (NEWCASTLE_BUILDING_ID, DEFAULT_SECTION_NEWCASTLE, DEFAULT_SECTION_OTHER_BUILDINGS),
            )
        except Exception:
            pass
        
        # Міграція: додати колонку building_id до таблиць голосування
        try:
            await db.execute("ALTER TABLE heating_votes ADD COLUMN building_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE water_votes ADD COLUMN building_id INTEGER DEFAULT NULL")
        except Exception:
            pass

        # Міграція: додати section_id до таблиць голосування
        try:
            await db.execute("ALTER TABLE heating_votes ADD COLUMN section_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE water_votes ADD COLUMN section_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute(
                """
                UPDATE heating_votes
                   SET section_id = CASE
                       WHEN building_id = ? THEN ?
                       ELSE ?
                   END
                 WHERE section_id IS NULL
                   AND building_id IS NOT NULL
                """,
                (NEWCASTLE_BUILDING_ID, DEFAULT_SECTION_NEWCASTLE, DEFAULT_SECTION_OTHER_BUILDINGS),
            )
            await db.execute(
                """
                UPDATE water_votes
                   SET section_id = CASE
                       WHEN building_id = ? THEN ?
                       ELSE ?
                   END
                 WHERE section_id IS NULL
                   AND building_id IS NOT NULL
                """,
                (NEWCASTLE_BUILDING_ID, DEFAULT_SECTION_NEWCASTLE, DEFAULT_SECTION_OTHER_BUILDINGS),
            )
        except Exception:
            pass

        # Міграція: додати building_id/section_id до подій (legacy події прив'язуємо до Ньюкасл секція 2)
        try:
            await db.execute("ALTER TABLE events ADD COLUMN building_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE events ADD COLUMN section_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute(
                """
                UPDATE events
                   SET building_id = ?, section_id = ?
                 WHERE building_id IS NULL
                   AND section_id IS NULL
                """,
                (NEWCASTLE_BUILDING_ID, DEFAULT_SECTION_NEWCASTLE),
            )
        except Exception:
            pass
        
        # === НОВА ТАБЛИЦЯ: Сенсори ESP32 ===
        await db.execute(
            """CREATE TABLE IF NOT EXISTS sensors (
                uuid TEXT PRIMARY KEY,
                building_id INTEGER NOT NULL,
                section_id INTEGER DEFAULT NULL,
                name TEXT,
                comment TEXT DEFAULT NULL,
                frozen_until TEXT DEFAULT NULL,
                frozen_is_up INTEGER DEFAULT NULL,
                frozen_at TEXT DEFAULT NULL,
                last_heartbeat TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (building_id) REFERENCES buildings(id)
            )"""
        )

        # Міграція: додати поля до sensors (для старих БД)
        try:
            await db.execute("ALTER TABLE sensors ADD COLUMN section_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE sensors ADD COLUMN comment TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE sensors ADD COLUMN frozen_until TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE sensors ADD COLUMN frozen_is_up INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE sensors ADD COLUMN frozen_at TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute(
                """
                UPDATE sensors
                   SET section_id = CASE
                       WHEN building_id = ? THEN ?
                       ELSE ?
                   END
                 WHERE section_id IS NULL
                """,
                (NEWCASTLE_BUILDING_ID, DEFAULT_SECTION_NEWCASTLE, DEFAULT_SECTION_OTHER_BUILDINGS),
            )
        except Exception:
            pass

        # Стабільні публічні числові ID сенсорів для external read-only API.
        # Не використовуємо rowid напряму (може мати "дірки"/бути нестабільним для зовнішніх інтеграцій).
        await db.execute(
            """CREATE TABLE IF NOT EXISTS sensor_public_ids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_uuid TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (sensor_uuid) REFERENCES sensors(uuid) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sensor_public_ids_sensor_uuid ON sensor_public_ids (sensor_uuid)"
        )
        try:
            now_iso = datetime.now().isoformat()
            await db.execute(
                """
                INSERT OR IGNORE INTO sensor_public_ids(sensor_uuid, created_at)
                SELECT s.uuid, COALESCE(s.created_at, ?)
                  FROM sensors s
                 ORDER BY s.rowid
                """,
                (now_iso,),
            )
        except Exception:
            pass
        
        # Таблиця для стану будинків (світло є/немає)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS building_power_state (
                building_id INTEGER PRIMARY KEY,
                is_up INTEGER DEFAULT 1,
                last_change TEXT,
                FOREIGN KEY (building_id) REFERENCES buildings(id)
            )"""
        )

        # Таблиця для стану секцій (building_id + section_id)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS building_section_power_state (
                building_id INTEGER NOT NULL,
                section_id INTEGER NOT NULL,
                is_up INTEGER DEFAULT 1,
                last_change TEXT,
                PRIMARY KEY (building_id, section_id),
                FOREIGN KEY (building_id) REFERENCES buildings(id)
            )"""
        )

        # ЯСНО: v2 кеш для секцій
        await db.execute(
            """CREATE TABLE IF NOT EXISTS yasno_schedule_state_v2 (
                building_id INTEGER NOT NULL,
                section_id INTEGER NOT NULL,
                queue_key TEXT NOT NULL,
                day_key TEXT NOT NULL,
                status TEXT DEFAULT NULL,
                slots_hash TEXT DEFAULT NULL,
                updated_at TEXT DEFAULT NULL,
                PRIMARY KEY (building_id, section_id, queue_key, day_key)
            )"""
        )

        # Індекси для секцій (корисно при масових розсилках/статистиці)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscribers_building_section ON subscribers (building_id, section_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sensors_building_section_active ON sensors (building_id, section_id, is_active)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_building_section_timestamp ON events (building_id, section_id, timestamp)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_heating_votes_building_section ON heating_votes (building_id, section_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_water_votes_building_section ON water_votes (building_id, section_id)"
        )

        # === БІЗНЕС-МОДУЛЬ: базові таблиці ===
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_owners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                tg_user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'owner',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                approved_at TEXT DEFAULT NULL,
                approved_by INTEGER DEFAULT NULL,
                UNIQUE (place_id, tg_user_id),
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL UNIQUE,
                tier TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                starts_at TEXT DEFAULT NULL,
                expires_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_subscription_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                tier TEXT NOT NULL,
                started_at TEXT NOT NULL,
                paid_until TEXT NOT NULL,
                source TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT DEFAULT NULL,
                close_reason TEXT DEFAULT NULL,
                purge_processed_at TEXT DEFAULT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                actor_tg_user_id INTEGER DEFAULT NULL,
                action TEXT NOT NULL,
                payload_json TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_payment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'telegram_stars',
                external_payment_id TEXT DEFAULT NULL,
                event_type TEXT NOT NULL,
                amount_stars INTEGER DEFAULT NULL,
                currency TEXT DEFAULT 'XTR',
                status TEXT NOT NULL DEFAULT 'new',
                raw_payload_json TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                processed_at TEXT DEFAULT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS business_claim_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                attempts_left INTEGER NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_by INTEGER DEFAULT NULL,
                used_at TEXT DEFAULT NULL,
                used_by INTEGER DEFAULT NULL,
                FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
            )"""
        )

        # Індекси бізнес-режиму
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_places_business_enabled_verified ON places (business_enabled, is_verified)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_places_service_published ON places (service_id, is_published)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_places_verified_tier ON places (verified_tier)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_owners_tg_user ON business_owners (tg_user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_owners_place_status ON business_owners (place_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_subscriptions_status_expires ON business_subscriptions (status, expires_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_sub_periods_place_started ON business_subscription_periods (place_id, started_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_sub_periods_place_purge ON business_subscription_periods (place_id, purge_processed_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_audit_place_created ON business_audit_log (place_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_payment_place_created ON business_payment_events (place_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_payment_external ON business_payment_events (provider, external_payment_id)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_payment_event ON business_payment_events (provider, external_payment_id, event_type)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_claim_token_place_status ON business_claim_tokens (place_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_claim_token_status_expires ON business_claim_tokens (status, expires_at)"
        )

        # === ADBOT: анонімні сигнатури запитів з чатів (без raw-text/user-id) ===
        await db.execute(
            """CREATE TABLE IF NOT EXISTS adbot_pattern_signatures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent TEXT NOT NULL,
                pattern TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                lang TEXT DEFAULT NULL,
                confidence_floor INTEGER NOT NULL DEFAULT 0,
                source_chat_tag TEXT DEFAULT NULL,
                stats_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(intent, pattern, source_chat_tag)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS adbot_pattern_stats_daily (
                date TEXT NOT NULL,
                chat_tag TEXT NOT NULL,
                intent TEXT NOT NULL,
                matched INTEGER NOT NULL DEFAULT 0,
                unmatched INTEGER NOT NULL DEFAULT 0,
                fp_flags INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (date, chat_tag, intent)
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_adbot_pattern_signatures_intent ON adbot_pattern_signatures (intent)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_adbot_pattern_signatures_chat_tag ON adbot_pattern_signatures (source_chat_tag)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_adbot_pattern_stats_daily_chat_intent ON adbot_pattern_stats_daily (chat_tag, intent, date)"
        )
        
        # buildings.has_sensor / buildings.sensor_count мають бути похідними
        # від активних записів sensors, а не "ручним" статичним станом.
        await _sync_building_sensor_stats_in_tx(db)
        await db.commit()

    # Після міграцій перебудовуємо keywords для всіх закладів
    await refresh_places_keywords()


async def db_set(k: str, v: str):
    """Зберегти значення за ключем."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "INSERT INTO kv(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, v),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def db_get(k: str) -> str | None:
    """Отримати значення за ключем."""
    async with open_db() as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def upsert_adbot_pattern_signature(
    *,
    intent: str,
    pattern: str,
    weight: float = 1.0,
    lang: str | None = None,
    confidence_floor: int = 0,
    source_chat_tag: str | None = None,
    stats_count_increment: int = 1,
) -> None:
    """Upsert anonymized adbot signature row (no raw message/user identifiers)."""
    now_iso = datetime.now().isoformat()

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                """
                INSERT INTO adbot_pattern_signatures(
                    intent, pattern, weight, lang, confidence_floor, source_chat_tag, stats_count, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent, pattern, source_chat_tag)
                DO UPDATE SET
                    weight=excluded.weight,
                    lang=excluded.lang,
                    confidence_floor=excluded.confidence_floor,
                    stats_count=adbot_pattern_signatures.stats_count + excluded.stats_count,
                    updated_at=excluded.updated_at
                """,
                (
                    str(intent or "").strip(),
                    str(pattern or "").strip(),
                    float(weight),
                    str(lang or "").strip() or None,
                    int(confidence_floor or 0),
                    str(source_chat_tag or "").strip() or None,
                    max(int(stats_count_increment or 0), 0),
                    now_iso,
                ),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def upsert_adbot_pattern_stats_daily(
    *,
    date: str,
    chat_tag: str,
    intent: str,
    matched_inc: int = 0,
    unmatched_inc: int = 0,
    fp_flags_inc: int = 0,
) -> None:
    """Upsert anonymized daily stats for adbot pattern mining."""
    now_iso = datetime.now().isoformat()

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                """
                INSERT INTO adbot_pattern_stats_daily(
                    date, chat_tag, intent, matched, unmatched, fp_flags, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, chat_tag, intent)
                DO UPDATE SET
                    matched=adbot_pattern_stats_daily.matched + excluded.matched,
                    unmatched=adbot_pattern_stats_daily.unmatched + excluded.unmatched,
                    fp_flags=adbot_pattern_stats_daily.fp_flags + excluded.fp_flags,
                    updated_at=excluded.updated_at
                """,
                (
                    str(date or "").strip(),
                    str(chat_tag or "").strip(),
                    str(intent or "").strip(),
                    max(int(matched_inc or 0), 0),
                    max(int(unmatched_inc or 0), 0),
                    max(int(fp_flags_inc or 0), 0),
                    now_iso,
                ),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


def sponsored_offers_enabled_key(chat_id: int) -> str:
    """KV key for resident preference: sponsored partner offers in main menu."""
    return f"sponsored_offers_enabled:{int(chat_id)}"


async def get_sponsored_offers_enabled(chat_id: int) -> bool:
    """Return whether sponsored partner offers are enabled for a resident."""
    raw = str((await db_get(sponsored_offers_enabled_key(chat_id))) or "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    return True


async def set_sponsored_offers_enabled(chat_id: int, enabled: bool) -> None:
    """Enable/disable sponsored partner offers for a resident."""
    await db_set(sponsored_offers_enabled_key(chat_id), "1" if enabled else "0")


def offers_digest_enabled_key(chat_id: int) -> str:
    """KV key for resident preference: weekly partner offers digest."""
    return f"offers_digest_enabled:{int(chat_id)}"


def offers_digest_last_sent_at_key(chat_id: int) -> str:
    """KV key for resident digest rate-limit timestamp (ISO datetime)."""
    return f"offers_digest_last_sent_at:{int(chat_id)}"


async def get_offers_digest_enabled(chat_id: int) -> bool:
    """Return whether weekly partner offers digest is enabled for a resident."""
    raw = str((await db_get(offers_digest_enabled_key(chat_id))) or "").strip().lower()
    if raw in {"1", "true", "on", "yes"}:
        return True
    return False


async def set_offers_digest_enabled(chat_id: int, enabled: bool) -> None:
    """Enable/disable weekly partner offers digest for a resident."""
    await db_set(offers_digest_enabled_key(chat_id), "1" if enabled else "0")


# ============ Admin Jobs Queue (control-plane) ============

_ADMIN_JOB_STATUSES = {"pending", "running", "done", "failed", "canceled"}


def _is_sqlite_locked_error(exc: BaseException) -> bool:
    if not isinstance(exc, (sqlite3.OperationalError, aiosqlite.OperationalError)):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


async def _with_sqlite_retry(fn, *, retries: int = 3, base_delay: float = 0.05):
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if not _is_sqlite_locked_error(exc) or attempt >= retries:
                raise
            delay = base_delay * (2**attempt)
            extra: dict[str, object] = {}
            try:
                code = getattr(fn, "__code__", None)
                if code is not None:
                    extra["fn_file"] = str(code.co_filename)
                    extra["fn_line"] = int(code.co_firstlineno)
                    extra["fn_name"] = str(getattr(fn, "__name__", ""))
            except Exception:
                extra = {}
            logger.warning("SQLite locked; retry %s/%s in %.2fs", attempt + 1, retries, delay)
            log_sqlite_lock_event(
                where="database._with_sqlite_retry",
                exc=exc,
                attempt=attempt + 1,
                retries=retries,
                delay_sec=delay,
                extra={k: v for k, v in extra.items() if v},
            )
            await asyncio.sleep(delay)
            attempt += 1


async def create_admin_job(kind: str, payload: dict, *, created_by: int | None = None) -> int:
    """Enqueue a new admin job to be executed by the main bot worker."""
    if not kind:
        raise ValueError("kind is required")

    created_at = datetime.now().isoformat()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))

    async def _op() -> int:
        async with open_db() as db:
            cur = await db.execute(
                """
                INSERT INTO admin_jobs (kind, payload_json, status, created_at, created_by)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (str(kind), payload_json, created_at, created_by),
            )
            await db.commit()
            return int(cur.lastrowid)

    return await _with_sqlite_retry(_op)


async def get_admin_job(job_id: int) -> dict | None:
    if not job_id:
        return None

    async def _op() -> dict | None:
        async with open_db() as db:
            async with db.execute(
                """
                SELECT id, kind, payload_json, status, created_at, created_by,
                       started_at, finished_at, updated_at, attempts,
                       progress_current, progress_total, last_error
                FROM admin_jobs
                WHERE id = ?
                """,
                (int(job_id),),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "kind": row[1],
            "payload": json.loads(row[2]) if row[2] else {},
            "status": row[3],
            "created_at": row[4],
            "created_by": row[5],
            "started_at": row[6],
            "finished_at": row[7],
            "updated_at": row[8],
            "attempts": row[9],
            "progress_current": row[10],
            "progress_total": row[11],
            "last_error": row[12],
        }

    return await _with_sqlite_retry(_op)


async def list_admin_jobs(limit: int = 20, offset: int = 0) -> list[dict]:
    # Allow larger exports (file) while keeping UI pages small.
    limit = max(1, min(int(limit), 5000))
    offset = max(0, int(offset))

    async def _op() -> list[dict]:
        async with open_db() as db:
            async with db.execute(
                """
                SELECT id, kind, status, created_at, created_by,
                       started_at, finished_at, updated_at, attempts,
                       progress_current, progress_total, last_error
                FROM admin_jobs
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "status": r[2],
                "created_at": r[3],
                "created_by": r[4],
                "started_at": r[5],
                "finished_at": r[6],
                "updated_at": r[7],
                "attempts": r[8],
                "progress_current": r[9],
                "progress_total": r[10],
                "last_error": r[11],
            }
            for r in rows
        ]

    return await _with_sqlite_retry(_op)


async def claim_next_admin_job() -> dict | None:
    """Atomically claim the next pending job and mark it running."""
    now = datetime.now().isoformat()

    async def _op() -> dict | None:
        async with open_db() as db:
            # Make claim atomic and avoid races if we ever scale.
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT id, kind, payload_json, created_by, attempts,
                       progress_current, progress_total
                FROM admin_jobs
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await db.execute("COMMIT")
                return None

            job_id = int(row[0])
            await db.execute(
                """
                UPDATE admin_jobs
                SET status='running',
                    started_at=COALESCE(started_at, ?),
                    updated_at=?,
                    attempts=attempts+1
                WHERE id=? AND status='pending'
                """,
                (now, now, job_id),
            )
            async with db.execute("SELECT changes()") as cur2:
                changes_row = await cur2.fetchone()
            changed = int(changes_row[0]) if changes_row else 0
            if changed != 1:
                await db.execute("ROLLBACK")
                return None

            await db.execute("COMMIT")

        payload = json.loads(row[2]) if row[2] else {}
        return {
            "id": job_id,
            "kind": row[1],
            "payload": payload,
            "created_by": row[3],
            "attempts": int(row[4]) + 1,
            "progress_current": row[5],
            "progress_total": row[6],
        }

    return await _with_sqlite_retry(_op)


async def update_admin_job_progress(job_id: int, *, current: int | None = None, total: int | None = None) -> None:
    if not job_id:
        return
    now = datetime.now().isoformat()

    current_sql = "progress_current=COALESCE(?, progress_current)" if current is not None else "progress_current=progress_current"
    total_sql = "progress_total=COALESCE(?, progress_total)" if total is not None else "progress_total=progress_total"

    params: list = []
    if current is not None:
        params.append(int(current))
    if total is not None:
        params.append(int(total))
    params.extend([now, int(job_id)])

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                f"""
                UPDATE admin_jobs
                SET {current_sql},
                    {total_sql},
                    updated_at=?
                WHERE id=?
                """,
                tuple(params),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def finish_admin_job(
    job_id: int,
    *,
    status: str,
    error: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
) -> None:
    """Mark job done/failed/canceled and persist error/progress."""
    if not job_id:
        return
    if status not in _ADMIN_JOB_STATUSES:
        raise ValueError(f"Invalid admin job status: {status}")
    if status in {"pending", "running"}:
        raise ValueError("finish_admin_job() requires a terminal status")

    now = datetime.now().isoformat()

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                """
                UPDATE admin_jobs
                SET status=?,
                    finished_at=?,
                    updated_at=?,
                    progress_current=COALESCE(?, progress_current),
                    progress_total=COALESCE(?, progress_total),
                    last_error=?
                WHERE id=?
                """,
                (
                    status,
                    now,
                    now,
                    progress_current,
                    progress_total,
                    error,
                    int(job_id),
                ),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def add_subscriber(
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None
):
    """
    Додати підписника з інформацією про користувача.
    Якщо підписник вже існує — оновлює інформацію.
    """
    async def _op() -> None:
        now = datetime.now().isoformat()
        async with open_db() as db:
            await db.execute(
                """INSERT INTO subscribers(chat_id, username, first_name, subscribed_at) 
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET 
                       username=excluded.username,
                       first_name=excluded.first_name,
                       subscribed_at=COALESCE(subscribers.subscribed_at, excluded.subscribed_at)""",
                (chat_id, username, first_name, now)
            )
            await db.commit()

    await _with_sqlite_retry(_op)


# ============ Функції для роботи з будинками ============

async def get_subscriber_building(chat_id: int) -> int | None:
    """Отримати ID будинку, на який підписаний користувач."""
    async with open_db() as db:
        async with db.execute(
            "SELECT building_id FROM subscribers WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_subscriber_building(chat_id: int, building_id: int) -> bool:
    """Встановити будинок для підписника. Повертає True якщо успішно."""
    async def _op() -> bool:
        async with open_db() as db:
            result = await db.execute(
                "UPDATE subscribers SET building_id = ? WHERE chat_id = ?",
                (building_id, chat_id)
            )
            await db.commit()
            return result.rowcount > 0

    return await _with_sqlite_retry(_op)


async def get_subscriber_section(chat_id: int) -> int | None:
    """Отримати номер секції, яку обрав користувач."""
    async with open_db() as db:
        async with db.execute(
            "SELECT section_id FROM subscribers WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_subscriber_section(chat_id: int, section_id: int | None) -> bool:
    """Встановити секцію для підписника. Повертає True якщо успішно."""
    async def _op() -> bool:
        async with open_db() as db:
            result = await db.execute(
                "UPDATE subscribers SET section_id = ? WHERE chat_id = ?",
                (section_id, chat_id),
            )
            await db.commit()
            return result.rowcount > 0

    return await _with_sqlite_retry(_op)


async def get_subscriber_building_and_section(chat_id: int) -> tuple[int | None, int | None]:
    """Отримати (building_id, section_id) для користувача одним запитом."""
    async with open_db() as db:
        async with db.execute(
            "SELECT building_id, section_id FROM subscribers WHERE chat_id=?",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return row[0], row[1]


async def get_building_info(building_id: int) -> dict | None:
    """Отримати інформацію про будинок з БД."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, name, address, has_sensor, sensor_count FROM buildings WHERE id=?",
            (building_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "address": row[2],
                    "has_sensor": bool(row[3]),
                    "sensor_count": row[4]
                }
            return None


async def get_all_buildings() -> list[dict]:
    """Отримати список всіх будинків."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, name, address, has_sensor, sensor_count FROM buildings ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "address": r[2],
                    "has_sensor": bool(r[3]),
                    "sensor_count": r[4],
                    "sections_count": get_building_section_count(int(r[0])),
                }
                for r in rows
            ]


async def get_subscribers_by_building(building_id: int = None) -> list[int] | dict[int, int]:
    """
    Отримати підписників.
    Якщо building_id вказано - повертає list[chat_id] для цього будинку.
    Якщо building_id=None - повертає dict{building_id: count} статистику по всіх будинках.
    """
    async with open_db() as db:
        if building_id is not None:
            async with db.execute(
                "SELECT chat_id FROM subscribers WHERE building_id = ?",
                (building_id,)
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]
        else:
            # Статистика по будинках
            async with db.execute(
                "SELECT building_id, COUNT(*) FROM subscribers GROUP BY building_id"
            ) as cur:
                rows = await cur.fetchall()
                return {r[0]: r[1] for r in rows}


async def remove_subscriber(chat_id: int):
    """Видалити підписника."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
            await db.commit()

    await _with_sqlite_retry(_op)


async def list_subscribers_full() -> list[dict]:
    """
    Отримати повну інформацію про всіх підписників.
    Повертає список словників з полями:
    chat_id, username, first_name, subscribed_at, quiet_start, quiet_end
    """
    async with open_db() as db:
        async with db.execute(
            "SELECT chat_id, username, first_name, subscribed_at, quiet_start, quiet_end FROM subscribers ORDER BY subscribed_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "chat_id": r[0],
                    "username": r[1],
                    "first_name": r[2],
                    "subscribed_at": r[3],
                    "quiet_start": r[4],
                    "quiet_end": r[5],
                }
                for r in rows
            ]


async def list_subscribers() -> list[int]:
    """Отримати список всіх підписників."""
    async with open_db() as db:
        async with db.execute("SELECT chat_id FROM subscribers") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


async def count_subscribers() -> int:
    """Підрахувати кількість підписників."""
    async with open_db() as db:
        async with db.execute("SELECT COUNT(*) FROM subscribers") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_subscribers_stats_by_building_section() -> dict[int | None, dict[int | None, int]]:
    """Статистика підписників по (building_id, section_id).

    Повертає:
      {building_id: {section_id: count}}
    де building_id/section_id можуть бути None для legacy/необраних.
    """
    async with open_db() as db:
        async with db.execute(
            """
            SELECT building_id, section_id, COUNT(*)
              FROM subscribers
             GROUP BY building_id, section_id
            """
        ) as cur:
            rows = await cur.fetchall()

    result: dict[int | None, dict[int | None, int]] = {}
    for building_id, section_id, count in rows:
        b = building_id if building_id is not None else None
        s = section_id if section_id is not None else None
        result.setdefault(b, {})[s] = int(count)
    return result


# ============ Тихі години ============

async def set_quiet_hours(chat_id: int, start_hour: int | None, end_hour: int | None):
    """
    Встановити тихі години для користувача.
    start_hour, end_hour: години (0-23), або None для вимкнення.
    """
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "UPDATE subscribers SET quiet_start=?, quiet_end=? WHERE chat_id=?",
                (start_hour, end_hour, chat_id),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_quiet_hours(chat_id: int) -> tuple[int | None, int | None]:
    """
    Отримати тихі години для користувача.
    Повертає (start_hour, end_hour) або (None, None).
    """
    async with open_db() as db:
        async with db.execute(
            "SELECT quiet_start, quiet_end FROM subscribers WHERE chat_id=?",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1]
            return None, None


def _is_in_quiet_hours(current_hour: int, quiet_start: int | None, quiet_end: int | None) -> bool:
    """Return True when current hour is inside user's quiet hours."""
    if quiet_start is None or quiet_end is None:
        return False
    if quiet_start <= quiet_end:
        return quiet_start <= current_hour < quiet_end
    return current_hour >= quiet_start or current_hour < quiet_end


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value))
    except Exception:
        return None


async def get_subscribers_for_notification(current_hour: int) -> list[int]:
    """
    Отримати список підписників, яким можна надсилати сповіщення зараз.
    Враховує тихі години кожного користувача.
    """
    async with open_db() as db:
        async with db.execute(
            "SELECT chat_id, quiet_start, quiet_end FROM subscribers"
        ) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end in rows:
        if not _is_in_quiet_hours(current_hour, quiet_start, quiet_end):
            result.append(chat_id)
    
    return result


# ============ Налаштування сповіщень ============

async def set_light_notifications(chat_id: int, enabled: bool):
    """Увімкнути/вимкнути сповіщення про світло."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "UPDATE subscribers SET light_notifications=? WHERE chat_id=?",
                (1 if enabled else 0, chat_id),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def set_alert_notifications(chat_id: int, enabled: bool):
    """Увімкнути/вимкнути сповіщення про тривоги."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "UPDATE subscribers SET alert_notifications=? WHERE chat_id=?",
                (1 if enabled else 0, chat_id),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def set_schedule_notifications(chat_id: int, enabled: bool):
    """Увімкнути/вимкнути сповіщення про графіки ЯСНО."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "UPDATE subscribers SET schedule_notifications=? WHERE chat_id=?",
                (1 if enabled else 0, chat_id),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_notification_settings(chat_id: int) -> dict:
    """
    Отримати налаштування сповіщень для користувача.
    Повертає словник з ключами: light_notifications, alert_notifications,
    schedule_notifications, sponsored_offers_enabled, offers_digest_enabled,
    quiet_start, quiet_end
    """
    async with open_db() as db:
        async with db.execute(
            "SELECT v FROM kv WHERE k=?",
            (sponsored_offers_enabled_key(chat_id),),
        ) as cur:
            sponsored_row = await cur.fetchone()
            sponsored_raw = str(sponsored_row[0]) if sponsored_row and sponsored_row[0] is not None else ""

        sponsored_enabled = sponsored_raw.strip().lower() not in {"0", "false", "off", "no"}

        async with db.execute(
            "SELECT v FROM kv WHERE k=?",
            (offers_digest_enabled_key(chat_id),),
        ) as cur:
            digest_row = await cur.fetchone()
            digest_raw = str(digest_row[0]) if digest_row and digest_row[0] is not None else ""

        offers_digest_enabled = digest_raw.strip().lower() in {"1", "true", "on", "yes"}

        async with db.execute(
            """SELECT light_notifications, alert_notifications, schedule_notifications, quiet_start, quiet_end 
               FROM subscribers WHERE chat_id=?""",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "light_notifications": bool(row[0]) if row[0] is not None else True,
                    "alert_notifications": bool(row[1]) if row[1] is not None else True,
                    "schedule_notifications": bool(row[2]) if row[2] is not None else True,
                    "sponsored_offers_enabled": sponsored_enabled,
                    "offers_digest_enabled": offers_digest_enabled,
                    "quiet_start": row[3],
                    "quiet_end": row[4],
                }
            return {
                "light_notifications": True,
                "alert_notifications": True,
                "schedule_notifications": True,
                "sponsored_offers_enabled": sponsored_enabled,
                "offers_digest_enabled": offers_digest_enabled,
                "quiet_start": None,
                "quiet_end": None,
            }


async def get_subscribers_for_light_notification(
    current_hour: int,
    building_id: int | None = None,
    section_id: int | None = None,
) -> list[int]:
    """
    Отримати список підписників для сповіщень про світло.
    Враховує тихі години, налаштування light_notifications та будинок.
    
    Args:
        current_hour: поточна година для перевірки тихих годин
        building_id: ID будинку для фільтрації (якщо None - повертає для Ньюкасла)
        section_id: номер секції для фільтрації (якщо None - всі секції будинку)
    """
    # Якщо building_id не вказано - використовуємо Ньюкасл (поточна реалізація)
    if building_id is None:
        building_id = NEWCASTLE_BUILDING_ID
    
    async with open_db() as db:
        if section_id is None:
            query = (
                """SELECT chat_id, quiet_start, quiet_end, light_notifications
                   FROM subscribers
                   WHERE (light_notifications = 1 OR light_notifications IS NULL)
                     AND building_id = ?"""
            )
            params = (building_id,)
        else:
            query = (
                """SELECT chat_id, quiet_start, quiet_end, light_notifications
                   FROM subscribers
                   WHERE (light_notifications = 1 OR light_notifications IS NULL)
                     AND building_id = ?
                     AND section_id = ?"""
            )
            params = (building_id, section_id)

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end, _ in rows:
        if not _is_in_quiet_hours(current_hour, quiet_start, quiet_end):
            result.append(chat_id)
    
    return result


async def get_subscribers_for_schedule_notification(
    current_hour: int,
    building_id: int | None = None,
    section_id: int | None = None,
) -> list[int]:
    """
    Отримати список підписників для сповіщень про оновлення графіків.
    Враховує тихі години, налаштування schedule_notifications та будинок.
    """
    if building_id is None:
        building_id = NEWCASTLE_BUILDING_ID

    async with open_db() as db:
        if section_id is None:
            query = (
                """SELECT chat_id, quiet_start, quiet_end, schedule_notifications
                   FROM subscribers
                   WHERE (schedule_notifications = 1 OR schedule_notifications IS NULL)
                     AND building_id = ?"""
            )
            params = (building_id,)
        else:
            query = (
                """SELECT chat_id, quiet_start, quiet_end, schedule_notifications
                   FROM subscribers
                   WHERE (schedule_notifications = 1 OR schedule_notifications IS NULL)
                     AND building_id = ?
                     AND section_id = ?"""
            )
            params = (building_id, section_id)

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    result = []
    for chat_id, quiet_start, quiet_end, _ in rows:
        if not _is_in_quiet_hours(current_hour, quiet_start, quiet_end):
            result.append(chat_id)

    return result


async def get_subscribers_for_alert_notification(current_hour: int) -> list[int]:
    """
    Отримати список підписників для сповіщень про тривоги.
    Враховує тихі години та налаштування alert_notifications.
    """
    async with open_db() as db:
        async with db.execute(
            """SELECT chat_id, quiet_start, quiet_end, alert_notifications 
               FROM subscribers WHERE alert_notifications = 1 OR alert_notifications IS NULL"""
        ) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end, _ in rows:
        if not _is_in_quiet_hours(current_hour, quiet_start, quiet_end):
            result.append(chat_id)
    
    return result


async def get_subscribers_for_offers_digest(
    current_hour: int,
    *,
    min_interval_hours: int = 7 * 24,
) -> list[int]:
    """Get subscribers eligible for partner offers digest.

    Eligibility rules:
    - resident opted-in (`offers_digest_enabled=1/true/on/yes`);
    - outside quiet hours;
    - last digest sent earlier than `min_interval_hours`.
    """
    interval_hours = max(1, int(min_interval_hours))
    cutoff = datetime.now() - timedelta(hours=interval_hours)

    async with open_db() as db:
        async with db.execute(
            """
            SELECT s.chat_id,
                   s.quiet_start,
                   s.quiet_end,
                   opt.v AS opt_in_value,
                   sent.v AS last_sent_at
              FROM subscribers s
         LEFT JOIN kv opt
                ON opt.k = ('offers_digest_enabled:' || s.chat_id)
         LEFT JOIN kv sent
                ON sent.k = ('offers_digest_last_sent_at:' || s.chat_id)
            """
        ) as cur:
            rows = await cur.fetchall()

    enabled_values = {"1", "true", "on", "yes"}
    result: list[int] = []
    for chat_id, quiet_start, quiet_end, opt_in_value, last_sent_at in rows:
        opt_norm = str(opt_in_value or "").strip().lower()
        if opt_norm not in enabled_values:
            continue
        if _is_in_quiet_hours(current_hour, quiet_start, quiet_end):
            continue
        last_sent = _parse_iso_datetime(str(last_sent_at or "").strip() or None)
        if last_sent is not None and last_sent > cutoff:
            continue
        result.append(int(chat_id))

    return result


async def mark_offers_digest_sent(
    chat_ids: list[int],
    *,
    sent_at: datetime | None = None,
) -> int:
    """Persist digest last-sent timestamp for successful recipients."""
    if not chat_ids:
        return 0

    unique_set: set[int] = set()
    for cid in chat_ids:
        try:
            value = int(cid)
        except Exception:
            continue
        if value > 0:
            unique_set.add(value)
    unique_ids = sorted(unique_set)
    if not unique_ids:
        return 0

    timestamp = (sent_at or datetime.now()).isoformat()

    async def _op() -> int:
        async with open_db() as db:
            for chat_id in unique_ids:
                await db.execute(
                    "INSERT INTO kv(k,v) VALUES(?,?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                    (offers_digest_last_sent_at_key(chat_id), timestamp),
                )
            await db.commit()
        return len(unique_ids)

    return await _with_sqlite_retry(_op)


# ============ Історія подій ============

async def add_event(
    event_type: str,
    building_id: int | None = None,
    section_id: int | None = None,
) -> datetime:
    """
    Додати подію до історії.
    event_type: 'up' або 'down'
    Повертає timestamp події.
    """
    async def _op() -> datetime:
        now = datetime.now()
        async with open_db() as db:
            await db.execute(
                "INSERT INTO events (event_type, timestamp, building_id, section_id) VALUES (?, ?, ?, ?)",
                (event_type, now.isoformat(), building_id, section_id),
            )
            await db.commit()
        return now

    return await _with_sqlite_retry(_op)


async def get_last_event(
    event_type: str | None = None,
    building_id: int | None = None,
    section_id: int | None = None,
) -> tuple[str, datetime] | None:
    """
    Отримати останню подію.
    event_type: 'up', 'down' або None (будь-яка)
    Повертає (event_type, timestamp) або None.
    """
    async with open_db() as db:
        clauses: list[str] = []
        params: list[object] = []
        if event_type:
            clauses.append("event_type=?")
            params.append(event_type)
        if building_id is not None and section_id is not None:
            clauses.append("building_id=? AND section_id=?")
            params.extend([building_id, section_id])
        elif building_id is not None:
            clauses.append("building_id=?")
            params.append(building_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT event_type, timestamp FROM events {where} ORDER BY id DESC LIMIT 1"

        async with db.execute(query, tuple(params)) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], datetime.fromisoformat(row[1])
            return None


async def get_last_event_before(
    before: datetime,
    building_id: int | None = None,
    section_id: int | None = None,
) -> tuple[str, datetime] | None:
    """Отримати останню подію до вказаного часу."""
    async with open_db() as db:
        clauses = ["timestamp < ?"]
        params: list[object] = [before.isoformat()]
        if building_id is not None and section_id is not None:
            clauses.append("building_id=? AND section_id=?")
            params.extend([building_id, section_id])
        elif building_id is not None:
            clauses.append("building_id=?")
            params.append(building_id)

        query = f"SELECT event_type, timestamp FROM events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT 1"
        async with db.execute(query, tuple(params)) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], datetime.fromisoformat(row[1])
            return None


async def get_events_since(
    since: datetime,
    building_id: int | None = None,
    section_id: int | None = None,
) -> list[tuple[str, datetime]]:
    """Отримати всі події після вказаного часу."""
    async with open_db() as db:
        clauses = ["timestamp >= ?"]
        params: list[object] = [since.isoformat()]
        if building_id is not None and section_id is not None:
            clauses.append("building_id=? AND section_id=?")
            params.extend([building_id, section_id])
        elif building_id is not None:
            clauses.append("building_id=?")
            params.append(building_id)

        query = f"SELECT event_type, timestamp FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp"
        async with db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            return [(r[0], datetime.fromisoformat(r[1])) for r in rows]


async def get_all_events(
    building_id: int | None = None,
    section_id: int | None = None,
) -> list[tuple[str, datetime]]:
    """Отримати всі події."""
    async with open_db() as db:
        clauses: list[str] = []
        params: list[object] = []
        if building_id is not None and section_id is not None:
            clauses.append("building_id=? AND section_id=?")
            params.extend([building_id, section_id])
        elif building_id is not None:
            clauses.append("building_id=?")
            params.append(building_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT event_type, timestamp FROM events {where} ORDER BY timestamp"
        async with db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            return [(r[0], datetime.fromisoformat(r[1])) for r in rows]


async def get_last_events(
    limit: int = 2,
    building_id: int | None = None,
    section_id: int | None = None,
) -> list[tuple[str, datetime]]:
    """Отримати останні N подій (за замовчуванням 2)."""
    async with open_db() as db:
        clauses: list[str] = []
        params: list[object] = []
        if building_id is not None and section_id is not None:
            clauses.append("building_id=? AND section_id=?")
            params.extend([building_id, section_id])
        elif building_id is not None:
            clauses.append("building_id=?")
            params.append(building_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT event_type, timestamp FROM events {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)

        async with db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            return [(r[0], datetime.fromisoformat(r[1])) for r in rows]


# ============ Функції для категорій послуг ============

async def add_general_service(name: str) -> int:
    """Додати категорію послуг. Повертає ID."""
    async def _op() -> int:
        async with open_db() as db:
            cursor = await db.execute(
                "INSERT INTO general_services(name) VALUES(?)",
                (name,)
            )
            await db.commit()
            return int(cursor.lastrowid)

    return await _with_sqlite_retry(_op)


async def edit_general_service(service_id: int, name: str) -> bool:
    """Редагувати назву категорії. Повертає True якщо успішно."""
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "UPDATE general_services SET name=? WHERE id=?",
                (name, service_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def delete_general_service(service_id: int) -> bool:
    """Видалити категорію. Повертає True якщо успішно."""
    async def _op() -> bool:
        async with open_db() as db:
            # Спочатку видаляємо всі заклади цієї категорії
            await db.execute("DELETE FROM places WHERE service_id=?", (service_id,))
            cursor = await db.execute(
                "DELETE FROM general_services WHERE id=?",
                (service_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def get_all_general_services() -> list[dict]:
    """Отримати всі категорії послуг."""
    async with open_db() as db:
        async with db.execute(
            """
            SELECT s.id, s.name
              FROM general_services s
             WHERE EXISTS (
                   SELECT 1 FROM places p WHERE p.service_id = s.id AND p.is_published = 1
             )
             ORDER BY s.name
            """
        ) as cur:
            rows = await cur.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]


async def get_general_service(service_id: int) -> dict | None:
    """Отримати категорію за ID."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, name FROM general_services WHERE id=?",
            (service_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"id": row[0], "name": row[1]}
            return None


# ============ Функції для закладів ============

async def add_place(service_id: int, name: str, description: str, address: str, keywords: str = None) -> int:
    """Додати заклад. Повертає ID."""
    merged_keywords = build_keywords(name, description, keywords)
    async def _op() -> int:
        async with open_db() as db:
            cursor = await db.execute(
                "INSERT INTO places(service_id, name, description, address, keywords) VALUES(?, ?, ?, ?, ?)",
                (service_id, name, description, address, merged_keywords)
            )
            await db.commit()
            return int(cursor.lastrowid)

    return await _with_sqlite_retry(_op)


async def edit_place(place_id: int, service_id: int, name: str, description: str, address: str, keywords: str = None) -> bool:
    """Редагувати заклад. Повертає True якщо успішно."""
    merged_keywords = build_keywords(name, description, keywords)
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "UPDATE places SET service_id=?, name=?, description=?, address=?, keywords=? WHERE id=?",
                (service_id, name, description, address, merged_keywords, place_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def refresh_places_keywords() -> None:
    """Перебудувати keywords для всіх закладів (name + description + keywords)."""
    async def _op() -> None:
        async with open_db() as db:
            async with db.execute("SELECT id, name, description, keywords FROM places") as cur:
                rows = await cur.fetchall()
            for row in rows:
                place_id, name, description, keywords = row
                merged = build_keywords(name, description, keywords)
                await db.execute("UPDATE places SET keywords=? WHERE id=?", (merged, place_id))
            await db.commit()

    await _with_sqlite_retry(_op)


async def update_place_keywords(place_id: int, keywords: str) -> bool:
    """Оновити тільки ключові слова закладу."""
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "UPDATE places SET keywords=? WHERE id=?",
                (keywords, place_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def delete_place(place_id: int) -> bool:
    """Видалити заклад. Повертає True якщо успішно."""
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "DELETE FROM places WHERE id=?",
                (place_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def get_places_by_service(service_id: int) -> list[dict]:
    """Отримати всі заклади певної категорії."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, service_id, name, description, address, keywords FROM places WHERE service_id=? AND is_published=1 ORDER BY name",
            (service_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], "address": r[4], "keywords": r[5]}
                for r in rows
            ]


async def get_all_places() -> list[dict]:
    """Отримати всі заклади."""
    async with open_db() as db:
        async with db.execute(
            """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords, s.name as service_name
               FROM places p
               JOIN general_services s ON p.service_id = s.id
               WHERE p.is_published = 1
               ORDER BY s.name, p.name"""
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], "address": r[4], "keywords": r[5], "service_name": r[6]}
                for r in rows
            ]


async def get_all_places_with_likes() -> list[dict]:
    """Отримати всі заклади з кількістю лайків."""
    async with open_db() as db:
        async with db.execute(
            """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                      s.name as service_name,
                      COALESCE(l.likes_count, 0) as likes_count
               FROM places p
               JOIN general_services s ON p.service_id = s.id
               LEFT JOIN (
                   SELECT place_id, COUNT(*) as likes_count
                   FROM place_likes
                   GROUP BY place_id
               ) l ON p.id = l.place_id
               WHERE p.is_published = 1
               ORDER BY s.name, p.name"""
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r[0], "service_id": r[1], "name": r[2], "description": r[3],
                    "address": r[4], "keywords": r[5], "service_name": r[6], "likes_count": r[7]
                }
                for r in rows
            ]


async def search_places(query: str) -> list[dict]:
    """Пошук закладів за назвою, описом, адресою або ключовими словами.

    - Токенізуємо запит (видаляємо розділові знаки), щоб знаходити навіть «де будівельний?».
    - Використовуємо OR по токенах, щоб знаходити слово в будь-якому контексті.
    - Сортуємо за кількістю збігів, потім за лайками.
    """
    raw_tokens = tokenize_query(query)
    if not raw_tokens:
        return []
    # Прибираємо дублікати, зберігаючи порядок
    tokens = []
    for t in raw_tokens:
        if t not in tokens:
            tokens.append(t)

    where_conditions = []
    params: list[str] = []
    score_parts = []

    for token in tokens:
        like = f"%{token}%"
        condition = (
            "(UCFOLD(p.name) LIKE ? OR UCFOLD(p.description) LIKE ? "
            "OR UCFOLD(p.address) LIKE ? OR UCFOLD(COALESCE(p.keywords, '')) LIKE ?)"
        )
        where_conditions.append(condition)
        params.extend([like, like, like, like])

        score_parts.append(
            "CASE WHEN " + condition + " THEN 1 ELSE 0 END"
        )

    where_clause = " OR ".join(where_conditions)
    match_score_expr = " + ".join(score_parts) if score_parts else "0"

    sql = f"""SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                        s.name as service_name,
                        (SELECT COUNT(*) FROM place_likes pl WHERE pl.place_id = p.id) as likes_count,
                        {match_score_expr} as match_score
                 FROM places p
                 JOIN general_services s ON p.service_id = s.id
                 WHERE p.is_published = 1 AND ({where_clause})
                 ORDER BY match_score DESC, likes_count DESC, p.name
                 LIMIT 20"""

    # Параметри для match_score дублюють ті ж самі placeholders
    score_params = params.copy()
    all_params = params + score_params

    async with open_db() as db:
        async with db.execute(sql, all_params) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r[0], "service_id": r[1], "name": r[2], "description": r[3],
                    "address": r[4], "keywords": r[5], "service_name": r[6], "likes_count": r[7]
                }
                for r in rows
            ]


async def search_places_by_service(query: str, service_id: int) -> list[dict]:
    """Пошук закладів у межах категорії."""
    raw_tokens = tokenize_query(query)
    if not raw_tokens:
        return []
    tokens = []
    for t in raw_tokens:
        if t not in tokens:
            tokens.append(t)

    where_conditions = []
    params: list[str] = []
    score_parts = []

    for token in tokens:
        like = f"%{token}%"
        condition = (
            "(UCFOLD(p.name) LIKE ? OR UCFOLD(p.description) LIKE ? "
            "OR UCFOLD(p.address) LIKE ? OR UCFOLD(COALESCE(p.keywords, '')) LIKE ?)"
        )
        where_conditions.append(condition)
        params.extend([like, like, like, like])

        score_parts.append(
            "CASE WHEN " + condition + " THEN 1 ELSE 0 END"
        )

    where_clause = " OR ".join(where_conditions)
    match_score_expr = " + ".join(score_parts) if score_parts else "0"

    sql = f"""SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                        s.name as service_name,
                        (SELECT COUNT(*) FROM place_likes pl WHERE pl.place_id = p.id) as likes_count,
                        {match_score_expr} as match_score
                 FROM places p
                 JOIN general_services s ON p.service_id = s.id
                 WHERE p.service_id = ? AND p.is_published = 1 AND ({where_clause})
                 ORDER BY match_score DESC, likes_count DESC, p.name
                 LIMIT 20"""

    score_params = params.copy()
    all_params = [service_id] + params + score_params

    async with open_db() as db:
        async with db.execute(sql, all_params) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r[0], "service_id": r[1], "name": r[2], "description": r[3],
                    "address": r[4], "keywords": r[5], "service_name": r[6], "likes_count": r[7]
                }
                for r in rows
            ]


async def get_place(place_id: int) -> dict | None:
    """Отримати заклад за ID."""
    async with open_db() as db:
        # Backward compatible: older DB might not yet have business profile columns.
        try:
            async with db.execute(
                """
                SELECT id, service_id, name, description, address, keywords,
                       opening_hours, contact_type, contact_value, link_url, logo_url,
                       photo_1_url, photo_2_url, photo_3_url,
                       promo_code,
                       menu_url, order_url, offer_1_text, offer_2_text,
                       offer_1_image_url, offer_2_image_url
                  FROM places
                 WHERE id=? AND is_published=1
                """,
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            async with db.execute(
                "SELECT id, service_id, name, description, address, keywords FROM places WHERE id=? AND is_published=1",
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "service_id": row[1],
                "name": row[2],
                "description": row[3],
                "address": row[4],
                "keywords": row[5],
            }

        if not row:
            return None
        return {
            "id": row[0],
            "service_id": row[1],
            "name": row[2],
            "description": row[3],
            "address": row[4],
            "keywords": row[5],
            "opening_hours": row[6],
            "contact_type": row[7],
            "contact_value": row[8],
            "link_url": row[9],
            "logo_url": row[10],
            "photo_1_url": row[11],
            "photo_2_url": row[12],
            "photo_3_url": row[13],
            "promo_code": row[14],
            "menu_url": row[15],
            "order_url": row[16],
            "offer_1_text": row[17],
            "offer_2_text": row[18],
            "offer_1_image_url": row[19],
            "offer_2_image_url": row[20],
        }


async def get_place_gallery_media(place_id: int, *, limit: int = 20) -> list[dict]:
    """Return ordered place gallery items for published place."""
    safe_limit = max(1, min(int(limit), 50))
    async with open_db() as db:
        try:
            async with db.execute(
                """
                SELECT gm.id, gm.place_id, gm.media_ref, gm.position, gm.created_at
                  FROM place_gallery_media gm
                 WHERE gm.place_id = ?
                   AND EXISTS (
                       SELECT 1
                         FROM places p
                        WHERE p.id = gm.place_id
                          AND p.is_published = 1
                   )
                 ORDER BY gm.position ASC, gm.id ASC
                 LIMIT ?
                """,
                (int(place_id), safe_limit),
            ) as cur:
                rows = await cur.fetchall()
        except Exception as error:
            # Backward-compatible with old DB snapshots without place_gallery_media table.
            if "no such table" in str(error).lower():
                return []
            raise
    return [
        {
            "id": int(row[0]),
            "place_id": int(row[1]),
            "media_ref": str(row[2] or "").strip(),
            "position": int(row[3] or 0),
            "created_at": str(row[4] or ""),
        }
        for row in rows
        if str(row[2] or "").strip()
    ]


# ============ Функції для переглядів закладів ============

async def record_place_view(place_id: int) -> None:
    """Записати перегляд картки закладу (агрегація по днях).

    Це best-effort метрика: якщо таблиці немає або БД тимчасово заблокована,
    не повинно ламати основний UX.
    """

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                """
                INSERT INTO place_views_daily(place_id, day, views)
                VALUES(?, date('now','localtime'), 1)
                ON CONFLICT(place_id, day) DO UPDATE SET views = views + 1
                """,
                (place_id,),
            )
            await db.commit()

    try:
        await _with_sqlite_retry(_op)
    except Exception:
        logger.exception("Failed to record place view place_id=%s", place_id)


async def record_place_click(place_id: int, action: str) -> None:
    """Записати клік у картці закладу (агрегація по днях + action)."""
    normalized_action = str(action or "").strip().lower()
    if not normalized_action:
        return
    if len(normalized_action) > 32:
        normalized_action = normalized_action[:32]
    # Keep action names compact/safe: letters, digits, underscore.
    normalized_action = re.sub(r"[^a-z0-9_]+", "_", normalized_action).strip("_")
    if not normalized_action:
        return

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                """
                INSERT INTO place_clicks_daily(place_id, day, action, cnt)
                VALUES(?, date('now','localtime'), ?, 1)
                ON CONFLICT(place_id, day, action) DO UPDATE SET cnt = cnt + 1
                """,
                (int(place_id), normalized_action),
            )
            await db.commit()

    try:
        await _with_sqlite_retry(_op)
    except Exception:
        logger.exception("Failed to record place click place_id=%s action=%s", place_id, normalized_action)


# ============ Функції для репортів про заклади ============

_PLACE_REPORT_STATUSES = {"pending", "resolved", "rejected"}
_BUSINESS_SUPPORT_REQUEST_STATUSES = {"pending", "resolved", "rejected"}


async def create_place_report(
    *,
    place_id: int,
    reporter_tg_user_id: int,
    reporter_username: str | None,
    reporter_first_name: str | None,
    reporter_last_name: str | None,
    report_text: str,
) -> dict | None:
    text = str(report_text or "").strip()
    if not text:
        return None

    now = datetime.now().isoformat()

    async def _op() -> dict | None:
        async with open_db() as db:
            # Ensure place exists and is visible to residents.
            async with db.execute("SELECT id FROM places WHERE id=? AND is_published=1", (int(place_id),)) as cur:
                row = await cur.fetchone()
            if not row:
                return None

            cursor = await db.execute(
                """
                INSERT INTO place_reports (
                    place_id,
                    reporter_tg_user_id,
                    reporter_username,
                    reporter_first_name,
                    reporter_last_name,
                    report_text,
                    status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    int(place_id),
                    int(reporter_tg_user_id),
                    str(reporter_username or "").strip() or None,
                    str(reporter_first_name or "").strip() or None,
                    str(reporter_last_name or "").strip() or None,
                    text,
                    now,
                ),
            )
            report_id = int(cursor.lastrowid)
            await db.commit()
            return {
                "id": report_id,
                "place_id": int(place_id),
                "reporter_tg_user_id": int(reporter_tg_user_id),
                "report_text": text,
                "status": "pending",
                "created_at": now,
            }

    return await _with_sqlite_retry(_op)


async def list_place_reports(
    *,
    status: str | None = "pending",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    safe_limit = max(1, min(int(limit), 5000))
    safe_offset = max(0, int(offset))
    safe_status = str(status).strip().lower() if status is not None else None
    if safe_status is not None and safe_status not in _PLACE_REPORT_STATUSES:
        raise ValueError(f"Invalid place report status: {status}")

    where_sql = ""
    params: list[object] = []
    if safe_status is not None:
        where_sql = " WHERE pr.status = ? "
        params.append(safe_status)

    async def _op() -> tuple[list[dict], int]:
        async with open_db() as db:
            async with db.execute(
                f"""
                SELECT COUNT(*)
                  FROM place_reports pr
                {where_sql}
                """,
                tuple(params),
            ) as cur_count:
                count_row = await cur_count.fetchone()
            total = int(count_row[0]) if count_row else 0

            async with db.execute(
                f"""
                SELECT
                    pr.id,
                    pr.place_id,
                    p.service_id,
                    p.name,
                    p.address,
                    gs.name,
                    pr.reporter_tg_user_id,
                    pr.reporter_username,
                    pr.reporter_first_name,
                    pr.reporter_last_name,
                    pr.report_text,
                    pr.status,
                    pr.created_at,
                    pr.resolved_at,
                    pr.resolved_by,
                    COALESCE(bs.tier, 'free') AS subscription_tier,
                    COALESCE(bs.status, 'inactive') AS subscription_status,
                    bs.expires_at AS subscription_expires_at,
                    CASE
                        WHEN lower(COALESCE(bs.tier, '')) IN ('pro', 'partner')
                             AND lower(COALESCE(bs.status, '')) IN ('active', 'canceled')
                             AND bs.expires_at IS NOT NULL
                             AND julianday(replace(substr(bs.expires_at, 1, 19), 'T', ' ')) > julianday('now')
                        THEN 2
                        WHEN lower(COALESCE(bs.tier, '')) = 'light'
                             AND lower(COALESCE(bs.status, '')) IN ('active', 'canceled')
                             AND bs.expires_at IS NOT NULL
                             AND julianday(replace(substr(bs.expires_at, 1, 19), 'T', ' ')) > julianday('now')
                        THEN 1
                        ELSE 0
                    END AS priority_score
                FROM place_reports pr
                LEFT JOIN places p ON p.id = pr.place_id
                LEFT JOIN general_services gs ON gs.id = p.service_id
                LEFT JOIN business_subscriptions bs ON bs.place_id = pr.place_id
                {where_sql}
                ORDER BY priority_score DESC, pr.created_at DESC, pr.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_limit, safe_offset]),
            ) as cur:
                rows = await cur.fetchall()

        result = [
            {
                "id": int(r[0]),
                "place_id": int(r[1]) if r[1] is not None else 0,
                "service_id": int(r[2]) if r[2] is not None else 0,
                "place_name": r[3] or "",
                "place_address": r[4] or "",
                "service_name": r[5] or "",
                "reporter_tg_user_id": int(r[6]) if r[6] is not None else 0,
                "reporter_username": r[7] or "",
                "reporter_first_name": r[8] or "",
                "reporter_last_name": r[9] or "",
                "report_text": r[10] or "",
                "status": r[11] or "",
                "created_at": r[12] or "",
                "resolved_at": r[13] or "",
                "resolved_by": int(r[14]) if r[14] is not None else None,
                "subscription_tier": r[15] or "free",
                "subscription_status": r[16] or "inactive",
                "subscription_expires_at": r[17] or "",
                "priority_score": int(r[18]) if r[18] is not None else 0,
            }
            for r in rows
        ]
        return result, total

    return await _with_sqlite_retry(_op)


async def set_place_report_status(report_id: int, status: str, *, resolved_by: int | None = None) -> bool:
    safe_status = str(status or "").strip().lower()
    if safe_status not in _PLACE_REPORT_STATUSES:
        raise ValueError(f"Invalid place report status: {status}")
    now = datetime.now().isoformat()

    if safe_status == "pending":
        resolved_at = None
        resolved_by_value = None
    else:
        resolved_at = now
        resolved_by_value = int(resolved_by) if resolved_by is not None else None

    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                """
                UPDATE place_reports
                   SET status=?,
                       resolved_at=?,
                       resolved_by=?
                 WHERE id=?
                   AND status <> ?
                """,
                (
                    safe_status,
                    resolved_at,
                    resolved_by_value,
                    int(report_id),
                    safe_status,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


# ============ Пріоритетна підтримка Partner-власників ============

async def create_business_support_request(
    *,
    place_id: int,
    owner_tg_user_id: int,
    owner_username: str | None,
    owner_first_name: str | None,
    owner_last_name: str | None,
    message_text: str,
) -> dict | None:
    text = str(message_text or "").strip()
    if not text:
        return None

    now = datetime.now().isoformat()

    async def _op() -> dict | None:
        async with open_db() as db:
            async with db.execute(
                "SELECT id FROM places WHERE id=?",
                (int(place_id),),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None

            cursor = await db.execute(
                """
                INSERT INTO business_support_requests (
                    place_id,
                    owner_tg_user_id,
                    owner_username,
                    owner_first_name,
                    owner_last_name,
                    message_text,
                    status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    int(place_id),
                    int(owner_tg_user_id),
                    str(owner_username or "").strip() or None,
                    str(owner_first_name or "").strip() or None,
                    str(owner_last_name or "").strip() or None,
                    text,
                    now,
                ),
            )
            request_id = int(cursor.lastrowid)
            await db.commit()
            return {
                "id": request_id,
                "place_id": int(place_id),
                "owner_tg_user_id": int(owner_tg_user_id),
                "message_text": text,
                "status": "pending",
                "created_at": now,
            }

    return await _with_sqlite_retry(_op)


async def list_business_support_requests(
    *,
    status: str | None = "pending",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    safe_limit = max(1, min(int(limit), 5000))
    safe_offset = max(0, int(offset))
    safe_status = str(status).strip().lower() if status is not None else None
    if safe_status is not None and safe_status not in _BUSINESS_SUPPORT_REQUEST_STATUSES:
        raise ValueError(f"Invalid business support request status: {status}")

    where_sql = ""
    params: list[object] = []
    if safe_status is not None:
        where_sql = " WHERE sr.status = ? "
        params.append(safe_status)

    async def _op() -> tuple[list[dict], int]:
        async with open_db() as db:
            async with db.execute(
                f"""
                SELECT COUNT(*)
                  FROM business_support_requests sr
                {where_sql}
                """,
                tuple(params),
            ) as cur_count:
                count_row = await cur_count.fetchone()
            total = int(count_row[0]) if count_row else 0

            async with db.execute(
                f"""
                SELECT
                    sr.id,
                    sr.place_id,
                    p.service_id,
                    p.name,
                    p.address,
                    gs.name,
                    sr.owner_tg_user_id,
                    sr.owner_username,
                    sr.owner_first_name,
                    sr.owner_last_name,
                    sr.message_text,
                    sr.status,
                    sr.created_at,
                    sr.resolved_at,
                    sr.resolved_by
                FROM business_support_requests sr
                LEFT JOIN places p ON p.id = sr.place_id
                LEFT JOIN general_services gs ON gs.id = p.service_id
                {where_sql}
                ORDER BY sr.created_at DESC, sr.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_limit, safe_offset]),
            ) as cur:
                rows = await cur.fetchall()

        result = [
            {
                "id": int(r[0]),
                "place_id": int(r[1]) if r[1] is not None else 0,
                "service_id": int(r[2]) if r[2] is not None else 0,
                "place_name": r[3] or "",
                "place_address": r[4] or "",
                "service_name": r[5] or "",
                "owner_tg_user_id": int(r[6]) if r[6] is not None else 0,
                "owner_username": r[7] or "",
                "owner_first_name": r[8] or "",
                "owner_last_name": r[9] or "",
                "message_text": r[10] or "",
                "status": r[11] or "",
                "created_at": r[12] or "",
                "resolved_at": r[13] or "",
                "resolved_by": int(r[14]) if r[14] is not None else None,
            }
            for r in rows
        ]
        return result, total

    return await _with_sqlite_retry(_op)


async def set_business_support_request_status(
    request_id: int,
    status: str,
    *,
    resolved_by: int | None = None,
) -> bool:
    safe_status = str(status or "").strip().lower()
    if safe_status not in _BUSINESS_SUPPORT_REQUEST_STATUSES:
        raise ValueError(f"Invalid business support request status: {status}")
    now = datetime.now().isoformat()

    if safe_status == "pending":
        resolved_at = None
        resolved_by_value = None
    else:
        resolved_at = now
        resolved_by_value = int(resolved_by) if resolved_by is not None else None

    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                """
                UPDATE business_support_requests
                   SET status=?,
                       resolved_at=?,
                       resolved_by=?
                 WHERE id=?
                   AND status <> ?
                """,
                (
                    safe_status,
                    resolved_at,
                    resolved_by_value,
                    int(request_id),
                    safe_status,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


# ============ Функції для лайків закладів ============

async def like_place(place_id: int, chat_id: int) -> bool:
    """Поставити лайк закладу. Повертає True якщо лайк додано, False якщо вже був."""
    async def _op() -> bool:
        now = datetime.now().isoformat()
        async with open_db() as db:
            try:
                await db.execute(
                    "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
                    (place_id, chat_id, now)
                )
                await db.commit()
                return True
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError):
                # Duplicate like (PRIMARY KEY place_id+chat_id): not an error for caller.
                return False

    return await _with_sqlite_retry(_op)


async def unlike_place(place_id: int, chat_id: int) -> bool:
    """Забрати лайк із закладу. Повертає True якщо лайк видалено."""
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "DELETE FROM place_likes WHERE place_id=? AND chat_id=?",
                (place_id, chat_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def has_liked_place(place_id: int, chat_id: int) -> bool:
    """Перевірити чи користувач вже лайкнув заклад."""
    async with open_db() as db:
        async with db.execute(
            "SELECT 1 FROM place_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        ) as cur:
            return await cur.fetchone() is not None


async def get_place_likes_count(place_id: int) -> int:
    """Отримати кількість лайків закладу."""
    async with open_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM place_likes WHERE place_id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_places_by_service_with_likes(service_id: int) -> list[dict]:
    """Отримати заклади категорії з кількістю лайків, відсортовані за лайками.

    Включає заклади з primary service_id ТА заклади з secondary категорією
    через place_extra_categories.
    """
    async with open_db() as db:
        async with db.execute(
            """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                      COALESCE(l.likes_count, 0) as likes_count
               FROM places p
               LEFT JOIN (
                   SELECT place_id, COUNT(*) as likes_count
                   FROM place_likes
                   GROUP BY place_id
               ) l ON p.id = l.place_id
               WHERE (p.service_id = ?
                      OR p.id IN (SELECT place_id FROM place_extra_categories WHERE service_id = ?))
                     AND p.is_published = 1
               ORDER BY likes_count DESC, p.name ASC""",
            (service_id, service_id)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], 
                 "address": r[4], "keywords": r[5], "likes_count": r[6]}
                for r in rows
            ]


async def get_place_extra_categories(place_id: int) -> list[dict]:
    """Повернути список додаткових категорій для закладу."""
    async with open_db() as db:
        async with db.execute(
            """SELECT gs.id, gs.name
               FROM place_extra_categories pec
               JOIN general_services gs ON gs.id = pec.service_id
               WHERE pec.place_id = ?
               ORDER BY gs.name COLLATE NOCASE""",
            (place_id,)
        ) as cur:
            return [{"id": r[0], "name": r[1]} for r in await cur.fetchall()]


async def add_place_extra_category(place_id: int, service_id: int) -> bool:
    """Додати secondary категорію до закладу. Повертає True якщо додано."""
    async with open_db() as db:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO place_extra_categories (place_id, service_id) VALUES (?, ?)",
                (place_id, service_id),
            )
            await db.commit()
            return db.total_changes > 0
        except Exception:
            return False


async def remove_place_extra_category(place_id: int, service_id: int) -> bool:
    """Видалити secondary категорію з закладу."""
    async with open_db() as db:
        await db.execute(
            "DELETE FROM place_extra_categories WHERE place_id = ? AND service_id = ?",
            (place_id, service_id),
        )
        await db.commit()
        return db.total_changes > 0


async def get_partner_places_for_sponsored() -> list[dict]:
    """List active Partner places for optional sponsored row in resident menu."""
    async with open_db() as db:
        async with db.execute(
            """
            SELECT p.id, p.service_id, p.name,
                   COALESCE(l.likes_count, 0) AS likes_count
              FROM places p
              LEFT JOIN (
                    SELECT place_id, COUNT(*) AS likes_count
                      FROM place_likes
                     GROUP BY place_id
              ) l ON p.id = l.place_id
             WHERE p.is_published = 1
               AND COALESCE(p.business_enabled, 0) = 1
               AND COALESCE(p.is_verified, 0) = 1
               AND lower(COALESCE(p.verified_tier, '')) = 'partner'
             ORDER BY likes_count DESC, p.name COLLATE NOCASE ASC, p.id ASC
            """
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": int(r[0]),
                    "service_id": int(r[1]),
                    "name": str(r[2] or ""),
                    "likes_count": int(r[3] or 0),
                }
                for r in rows
            ]


async def has_any_published_verified_business_place() -> bool:
    """Return True when at least one published verified business place exists."""
    async with open_db() as db:
        async with db.execute(
            """
            SELECT 1
              FROM places
             WHERE is_published = 1
               AND COALESCE(business_enabled, 0) = 1
               AND COALESCE(is_verified, 0) = 1
             LIMIT 1
            """
        ) as cur:
            row = await cur.fetchone()
            return row is not None


# ============ Функції для укриттів ============

async def get_all_shelter_places() -> list[dict]:
    """Отримати всі укриття."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, name, description, address, keywords FROM shelter_places ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "name": r[1], "description": r[2], "address": r[3], "keywords": r[4]}
                for r in rows
            ]


async def get_shelter_place(place_id: int) -> dict | None:
    """Отримати укриття за ID."""
    async with open_db() as db:
        async with db.execute(
            "SELECT id, name, description, address, keywords FROM shelter_places WHERE id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"id": row[0], "name": row[1], "description": row[2], "address": row[3], "keywords": row[4]}
            return None


async def get_shelter_places_with_likes() -> list[dict]:
    """Отримати укриття з кількістю лайків."""
    async with open_db() as db:
        async with db.execute(
            """SELECT sp.id, sp.name, sp.description, sp.address, sp.keywords,
                      COALESCE(l.likes_count, 0) as likes_count
               FROM shelter_places sp
               LEFT JOIN (
                   SELECT place_id, COUNT(*) as likes_count
                   FROM shelter_likes
                   GROUP BY place_id
               ) l ON sp.id = l.place_id
               ORDER BY sp.name""",
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "name": r[1], "description": r[2], "address": r[3], "keywords": r[4], "likes_count": r[5]}
                for r in rows
            ]


async def like_shelter(place_id: int, chat_id: int) -> bool:
    """Поставити лайк укриттю. Повертає True якщо лайк додано, False якщо вже був."""
    async def _op() -> bool:
        now = datetime.now().isoformat()
        async with open_db() as db:
            try:
                await db.execute(
                    "INSERT INTO shelter_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
                    (place_id, chat_id, now)
                )
                await db.commit()
                return True
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError):
                # Duplicate like (PRIMARY KEY place_id+chat_id): not an error for caller.
                return False

    return await _with_sqlite_retry(_op)


async def unlike_shelter(place_id: int, chat_id: int) -> bool:
    """Забрати лайк із укриття. Повертає True якщо лайк видалено."""
    async def _op() -> bool:
        async with open_db() as db:
            cursor = await db.execute(
                "DELETE FROM shelter_likes WHERE place_id=? AND chat_id=?",
                (place_id, chat_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def has_liked_shelter(place_id: int, chat_id: int) -> bool:
    """Перевірити чи користувач вже лайкнув укриття."""
    async with open_db() as db:
        async with db.execute(
            "SELECT 1 FROM shelter_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        ) as cur:
            return await cur.fetchone() is not None


async def get_shelter_likes_count(place_id: int) -> int:
    """Отримати кількість лайків укриття."""
    async with open_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM shelter_likes WHERE place_id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============ Функції для голосування за опалення/воду ============

async def vote_heating(
    chat_id: int,
    has_heating: bool,
    building_id: int | None = None,
    section_id: int | None = None,
):
    """
    Проголосувати за стан опалення.
    Голос прив'язується до будинку користувача.
    """
    # Якщо building_id/section_id не вказано - отримуємо з профілю користувача
    if building_id is None or section_id is None:
        b, s = await get_subscriber_building_and_section(chat_id)
        building_id = building_id if building_id is not None else b
        section_id = section_id if section_id is not None else s
    
    if building_id is None:
        # Користувач не обрав будинок - не можемо зберегти голос
        return
    if section_id is None:
        section_id = default_section_for_building(building_id)
    if section_id not in VALID_SECTION_IDS:
        return
    
    async def _op() -> None:
        now = datetime.now().isoformat()
        async with open_db() as db:
            await db.execute(
                """INSERT INTO heating_votes(chat_id, has_heating, voted_at, building_id, section_id)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       has_heating=excluded.has_heating,
                       voted_at=excluded.voted_at,
                       building_id=excluded.building_id,
                       section_id=excluded.section_id""",
                (chat_id, 1 if has_heating else 0, now, building_id, section_id)
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def vote_water(
    chat_id: int,
    has_water: bool,
    building_id: int | None = None,
    section_id: int | None = None,
):
    """
    Проголосувати за стан води.
    Голос прив'язується до будинку користувача.
    """
    # Якщо building_id/section_id не вказано - отримуємо з профілю користувача
    if building_id is None or section_id is None:
        b, s = await get_subscriber_building_and_section(chat_id)
        building_id = building_id if building_id is not None else b
        section_id = section_id if section_id is not None else s
    
    if building_id is None:
        # Користувач не обрав будинок - не можемо зберегти голос
        return
    if section_id is None:
        section_id = default_section_for_building(building_id)
    if section_id not in VALID_SECTION_IDS:
        return
    
    async def _op() -> None:
        now = datetime.now().isoformat()
        async with open_db() as db:
            await db.execute(
                """INSERT INTO water_votes(chat_id, has_water, voted_at, building_id, section_id)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       has_water=excluded.has_water,
                       voted_at=excluded.voted_at,
                       building_id=excluded.building_id,
                       section_id=excluded.section_id""",
                (chat_id, 1 if has_water else 0, now, building_id, section_id)
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_heating_stats(building_id: int | None = None, section_id: int | None = None) -> dict:
    """
    Отримати статистику голосування за опалення.
    
    Args:
        building_id: ID будинку для фільтрації (якщо None - всі голоси)
    """
    async with open_db() as db:
        if building_id is not None and section_id is not None:
            query = (
                "SELECT has_heating, COUNT(*) FROM heating_votes "
                "WHERE building_id = ? AND section_id = ? "
                "GROUP BY has_heating"
            )
            params = (building_id, section_id)
        elif building_id is not None:
            query = "SELECT has_heating, COUNT(*) FROM heating_votes WHERE building_id = ? GROUP BY has_heating"
            params = (building_id,)
        else:
            query = "SELECT has_heating, COUNT(*) FROM heating_votes GROUP BY has_heating"
            params = ()
        
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            has = 0
            has_not = 0
            for row in rows:
                if row[0] == 1:
                    has = row[1]
                else:
                    has_not = row[1]
            total = has + has_not
            return {
                "has": has,
                "has_not": has_not,
                "total": total,
                "has_percent": round(has / total * 100) if total > 0 else 0,
                "has_not_percent": round(has_not / total * 100) if total > 0 else 0,
            }


async def get_water_stats(building_id: int | None = None, section_id: int | None = None) -> dict:
    """
    Отримати статистику голосування за воду.
    
    Args:
        building_id: ID будинку для фільтрації (якщо None - всі голоси)
    """
    async with open_db() as db:
        if building_id is not None and section_id is not None:
            query = (
                "SELECT has_water, COUNT(*) FROM water_votes "
                "WHERE building_id = ? AND section_id = ? "
                "GROUP BY has_water"
            )
            params = (building_id, section_id)
        elif building_id is not None:
            query = "SELECT has_water, COUNT(*) FROM water_votes WHERE building_id = ? GROUP BY has_water"
            params = (building_id,)
        else:
            query = "SELECT has_water, COUNT(*) FROM water_votes GROUP BY has_water"
            params = ()
        
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            has = 0
            has_not = 0
            for row in rows:
                if row[0] == 1:
                    has = row[1]
                else:
                    has_not = row[1]
            total = has + has_not
            return {
                "has": has,
                "has_not": has_not,
                "total": total,
                "has_percent": round(has / total * 100) if total > 0 else 0,
                "has_not_percent": round(has_not / total * 100) if total > 0 else 0,
            }


async def get_user_vote(
    chat_id: int,
    vote_type: str,
    building_id: int | None = None,
    section_id: int | None = None,
) -> bool | None:
    """Отримати голос користувача (heating або water) для його building+section."""
    table = "heating_votes" if vote_type == "heating" else "water_votes"
    column = "has_heating" if vote_type == "heating" else "has_water"

    if building_id is None or section_id is None:
        b, s = await get_subscriber_building_and_section(chat_id)
        building_id = building_id if building_id is not None else b
        section_id = section_id if section_id is not None else s
    if building_id is None or section_id is None:
        return None

    async with open_db() as db:
        async with db.execute(
            f"SELECT {column} FROM {table} WHERE chat_id=? AND building_id=? AND section_id=?",
            (chat_id, building_id, section_id),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0] == 1
            return None


async def reset_votes(building_id: int | None = None, section_id: int | None = None):
    """
    Скинути голоси (викликається при зміні стану світла).
    
    Args:
        building_id: ID будинку для скидання (якщо None - скидаємо всі голоси)
    """
    async def _op() -> None:
        async with open_db() as db:
            if building_id is not None and section_id is not None:
                await db.execute(
                    "DELETE FROM heating_votes WHERE building_id = ? AND section_id = ?",
                    (building_id, section_id),
                )
                await db.execute(
                    "DELETE FROM water_votes WHERE building_id = ? AND section_id = ?",
                    (building_id, section_id),
                )
            elif building_id is not None:
                await db.execute("DELETE FROM heating_votes WHERE building_id = ?", (building_id,))
                await db.execute("DELETE FROM water_votes WHERE building_id = ?", (building_id,))
            else:
                await db.execute("DELETE FROM heating_votes")
                await db.execute("DELETE FROM water_votes")
            await db.commit()

    await _with_sqlite_retry(_op)


# ============ Активні сповіщення для оновлення ============

async def save_notification(chat_id: int, message_id: int, notification_type: str = "power_change"):
    """Зберегти сповіщення для подальшого оновлення (одне на чат і тип)."""
    async def _op() -> None:
        now = datetime.now().isoformat()
        async with open_db() as db:
            await db.execute(
                "DELETE FROM active_notifications WHERE chat_id=? AND notification_type=?",
                (chat_id, notification_type)
            )
            await db.execute(
                "INSERT INTO active_notifications(chat_id, message_id, created_at, notification_type) VALUES(?, ?, ?, ?)",
                (chat_id, message_id, now, notification_type)
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_active_notifications(notification_type: str | None = None) -> list[dict]:
    """Отримати активні сповіщення для оновлення."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        if notification_type:
            query = "SELECT id, chat_id, message_id, created_at, notification_type FROM active_notifications WHERE notification_type=?"
            params = (notification_type,)
        else:
            query = "SELECT id, chat_id, message_id, created_at, notification_type FROM active_notifications"
            params = ()
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "chat_id": r["chat_id"],
                    "message_id": r["message_id"],
                    "created_at": r["created_at"],
                    "notification_type": r["notification_type"],
                }
                for r in rows
            ]


async def get_active_notifications_for_chat(chat_id: int) -> list[dict]:
    """Отримати всі активні сповіщення для конкретного чату."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, chat_id, message_id, created_at, notification_type FROM active_notifications WHERE chat_id=?",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "chat_id": r["chat_id"],
                    "message_id": r["message_id"],
                    "created_at": r["created_at"],
                    "notification_type": r["notification_type"],
                }
                for r in rows
            ]


async def delete_notification(notification_id: int):
    """Видалити сповіщення за ID."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute("DELETE FROM active_notifications WHERE id=?", (notification_id,))
            await db.commit()

    await _with_sqlite_retry(_op)


async def clear_all_notifications():
    """Видалити всі активні сповіщення (при зміні стану світла)."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute("DELETE FROM active_notifications")
            await db.commit()

    await _with_sqlite_retry(_op)


# ============ ЯСНО: кеш стану графіків ============

async def get_yasno_schedule_state(
    building_id: int,
    queue_key: str,
    day_key: str,
    section_id: int | None = None,
) -> dict | None:
    async with open_db() as db:
        if section_id is None:
            query = (
                """SELECT status, slots_hash, updated_at
                   FROM yasno_schedule_state
                   WHERE building_id=? AND queue_key=? AND day_key=?"""
            )
            params = (building_id, queue_key, day_key)
        else:
            query = (
                """SELECT status, slots_hash, updated_at
                   FROM yasno_schedule_state_v2
                   WHERE building_id=? AND section_id=? AND queue_key=? AND day_key=?"""
            )
            params = (building_id, section_id, queue_key, day_key)

        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "status": row[0],
                "slots_hash": row[1],
                "updated_at": row[2],
            }


async def upsert_yasno_schedule_state(
    building_id: int,
    section_id: int | None,
    queue_key: str,
    day_key: str,
    status: str | None,
    slots_hash: str | None,
    updated_at: str | None,
) -> None:
    async def _op() -> None:
        async with open_db() as db:
            if section_id is None:
                await db.execute(
                    """INSERT INTO yasno_schedule_state(building_id, queue_key, day_key, status, slots_hash, updated_at)
                       VALUES(?, ?, ?, ?, ?, ?)
                       ON CONFLICT(building_id, queue_key, day_key)
                       DO UPDATE SET status=excluded.status, slots_hash=excluded.slots_hash, updated_at=excluded.updated_at""",
                    (building_id, queue_key, day_key, status, slots_hash, updated_at),
                )
            else:
                await db.execute(
                    """INSERT INTO yasno_schedule_state_v2(building_id, section_id, queue_key, day_key, status, slots_hash, updated_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(building_id, section_id, queue_key, day_key)
                       DO UPDATE SET status=excluded.status, slots_hash=excluded.slots_hash, updated_at=excluded.updated_at""",
                    (building_id, section_id, queue_key, day_key, status, slots_hash, updated_at),
                )
            await db.commit()

    await _with_sqlite_retry(_op)


# ============ Останнє повідомлення бота (чистий чат) ============

async def save_last_bot_message(chat_id: int, message_id: int):
    """Зберегти останнє повідомлення бота для чату."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                "INSERT INTO last_bot_message(chat_id, message_id) VALUES(?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET message_id=excluded.message_id",
                (chat_id, message_id)
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_last_bot_message(chat_id: int) -> int | None:
    """Отримати ID останнього повідомлення бота для чату."""
    async with open_db() as db:
        async with db.execute("SELECT message_id FROM last_bot_message WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def delete_last_bot_message_record(chat_id: int):
    """Видалити запис про останнє повідомлення."""
    async def _op() -> None:
        async with open_db() as db:
            await db.execute("DELETE FROM last_bot_message WHERE chat_id=?", (chat_id,))
            await db.commit()

    await _with_sqlite_retry(_op)


# ============ Сенсори ESP32 ============


async def _sync_building_sensor_stats_in_tx(
    db: aiosqlite.Connection,
    building_ids: set[int] | list[int] | tuple[int, ...] | None = None,
) -> None:
    """Sync derived fields buildings.has_sensor/sensor_count inside current transaction."""
    if building_ids is None:
        async with db.execute("SELECT id FROM buildings ORDER BY id") as cur:
            rows = await cur.fetchall()
        ids = [int(r[0]) for r in rows]
    else:
        ids = sorted({int(bid) for bid in building_ids if bid is not None})

    for bid in ids:
        async with db.execute(
            "SELECT COUNT(*) FROM sensors WHERE building_id=? AND is_active=1",
            (bid,),
        ) as cur:
            row = await cur.fetchone()
        sensor_count = int(row[0]) if row and row[0] is not None else 0
        has_sensor = 1 if sensor_count > 0 else 0
        await db.execute(
            """
            UPDATE buildings
               SET has_sensor=?,
                   sensor_count=?
             WHERE id=?
               AND (has_sensor IS NOT ? OR sensor_count IS NOT ?)
            """,
            (has_sensor, sensor_count, bid, has_sensor, sensor_count),
        )


async def sync_building_sensor_stats(building_id: int | None = None) -> None:
    """Force-sync sensor counters for one building or for all buildings."""

    async def _op() -> None:
        async with open_db() as db:
            if building_id is None:
                await _sync_building_sensor_stats_in_tx(db)
            else:
                await _sync_building_sensor_stats_in_tx(db, {int(building_id)})
            await db.commit()

    await _with_sqlite_retry(_op)

async def upsert_sensor_heartbeat(
    uuid: str,
    building_id: int,
    section_id: int | None,
    name: str | None = None,
    comment: str | None = None,
) -> bool:
    """
    Upsert сенсора + оновити last_heartbeat.
    Повертає True якщо сенсор був створений, False якщо оновлений.
    """
    async def _op() -> bool:
        async with open_db() as db:
            now = datetime.now().isoformat()
            async with db.execute(
                "SELECT building_id, is_active FROM sensors WHERE uuid=?",
                (uuid,),
            ) as cur:
                prev_row = await cur.fetchone()
            existed = prev_row is not None
            prev_building_id = int(prev_row[0]) if prev_row and prev_row[0] is not None else None
            prev_is_active = bool(prev_row[1]) if prev_row and prev_row[1] is not None else False

            await db.execute(
                """
                INSERT INTO sensors(uuid, building_id, section_id, name, comment, last_heartbeat, created_at, is_active)
                VALUES(?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(uuid) DO UPDATE SET
                    building_id=excluded.building_id,
                    section_id=excluded.section_id,
                    name=COALESCE(excluded.name, sensors.name),
                    comment=COALESCE(excluded.comment, sensors.comment),
                    last_heartbeat=excluded.last_heartbeat,
                    is_active=1
                """,
                (uuid, building_id, section_id, name, comment, now, now),
            )

            sync_building_ids: set[int] = set()
            if not existed:
                sync_building_ids.add(int(building_id))
            else:
                if prev_building_id is not None and prev_building_id != int(building_id):
                    sync_building_ids.add(prev_building_id)
                    sync_building_ids.add(int(building_id))
                elif not prev_is_active:
                    sync_building_ids.add(int(building_id))
            if sync_building_ids:
                await _sync_building_sensor_stats_in_tx(db, sync_building_ids)
            await db.commit()
            return not existed

    return await _with_sqlite_retry(_op)


async def register_sensor(uuid: str, building_id: int, name: str | None = None) -> bool:
    """
    Реєстрація нового сенсора або оновлення існуючого.
    Повертає True якщо сенсор новий, False якщо оновлений.
    """
    async def _op() -> bool:
        async with open_db() as db:
            now = datetime.now().isoformat()

            # Перевіряємо чи сенсор вже існує
            async with db.execute(
                "SELECT building_id, is_active FROM sensors WHERE uuid=?",
                (uuid,),
            ) as cur:
                prev_row = await cur.fetchone()

            if prev_row:
                prev_building_id = int(prev_row[0]) if prev_row[0] is not None else None
                prev_is_active = bool(prev_row[1]) if prev_row[1] is not None else False
                # Оновлюємо існуючий сенсор
                await db.execute(
                    "UPDATE sensors SET building_id=?, name=?, is_active=1 WHERE uuid=?",
                    (building_id, name, uuid)
                )
                sync_building_ids: set[int] = set()
                if prev_building_id is not None and prev_building_id != int(building_id):
                    sync_building_ids.add(prev_building_id)
                    sync_building_ids.add(int(building_id))
                elif not prev_is_active:
                    sync_building_ids.add(int(building_id))
                if sync_building_ids:
                    await _sync_building_sensor_stats_in_tx(db, sync_building_ids)
                await db.commit()
                return False

            # Створюємо новий сенсор
            await db.execute(
                "INSERT INTO sensors(uuid, building_id, name, created_at, is_active) VALUES(?, ?, ?, ?, 1)",
                (uuid, building_id, name, now)
            )
            await _sync_building_sensor_stats_in_tx(db, {int(building_id)})
            await db.commit()
            return True

    return await _with_sqlite_retry(_op)


async def update_sensor_heartbeat(uuid: str) -> bool:
    """
    Оновити час останнього heartbeat сенсора.
    Повертає True якщо сенсор знайдено, False якщо ні.
    """
    async def _op() -> bool:
        async with open_db() as db:
            now = datetime.now().isoformat()
            cursor = await db.execute(
                "UPDATE sensors SET last_heartbeat=? WHERE uuid=? AND is_active=1",
                (now, uuid)
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _with_sqlite_retry(_op)


async def get_sensor_by_uuid(uuid: str) -> dict | None:
    """Отримати сенсор за UUID."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT uuid, building_id, section_id, name, comment,
                   frozen_until, frozen_is_up, frozen_at,
                   last_heartbeat, created_at, is_active
              FROM sensors
             WHERE uuid=?
            """,
            (uuid,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                    "is_active": bool(row["is_active"]),
                }
            return None


async def _ensure_sensor_public_ids(active_only: bool = False) -> None:
    """Backfill mapping table sensor_public_ids for existing sensors."""
    where_clause = "WHERE s.is_active=1" if active_only else ""
    now_iso = datetime.now().isoformat()

    async def _op() -> None:
        async with open_db() as db:
            await db.execute(
                f"""
                INSERT OR IGNORE INTO sensor_public_ids(sensor_uuid, created_at)
                SELECT s.uuid, COALESCE(s.created_at, ?)
                  FROM sensors s
                  {where_clause}
                 ORDER BY s.rowid
                """,
                (now_iso,),
            )
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_active_sensor_by_public_id(public_id: int) -> dict | None:
    """Отримати активний сенсор за стабільним публічним ID."""
    await _ensure_sensor_public_ids(active_only=True)
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT spi.id AS public_id,
                   s.uuid, s.building_id, s.section_id, s.name, s.comment,
                   s.frozen_until, s.frozen_is_up, s.frozen_at,
                   s.last_heartbeat, s.created_at, s.is_active
              FROM sensor_public_ids spi
              JOIN sensors s ON s.uuid = spi.sensor_uuid
             WHERE spi.id=?
               AND s.is_active=1
            """,
            (public_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "public_id": row["public_id"],
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                    "is_active": bool(row["is_active"]),
                }
            return None


async def get_all_active_sensors_with_public_ids() -> list[dict]:
    """Отримати всі активні сенсори разом зі стабільним публічним ID."""
    await _ensure_sensor_public_ids(active_only=True)
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT spi.id AS public_id,
                   s.uuid, s.building_id, s.section_id, s.name, s.comment,
                   s.frozen_until, s.frozen_is_up, s.frozen_at,
                   s.last_heartbeat, s.created_at
              FROM sensor_public_ids spi
              JOIN sensors s ON s.uuid = spi.sensor_uuid
             WHERE s.is_active=1
             ORDER BY spi.id
            """
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "public_id": row["public_id"],
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def get_sensors_by_building(building_id: int) -> list[dict]:
    """Отримати всі активні сенсори будинку."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT uuid, building_id, section_id, name, comment,
                   frozen_until, frozen_is_up, frozen_at,
                   last_heartbeat, created_at
              FROM sensors
             WHERE building_id=? AND is_active=1
            """,
            (building_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def get_sensors_by_building_section(building_id: int, section_id: int) -> list[dict]:
    """Отримати всі активні сенсори конкретної секції."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT uuid, building_id, section_id, name, comment,
                   frozen_until, frozen_is_up, frozen_at,
                   last_heartbeat, created_at
              FROM sensors
             WHERE building_id=?
               AND section_id=?
               AND is_active=1
            """,
            (building_id, section_id),
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def get_all_active_sensors() -> list[dict]:
    """Отримати всі активні сенсори."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT uuid, building_id, section_id, name, comment,
                   frozen_until, frozen_is_up, frozen_at,
                   last_heartbeat, created_at
              FROM sensors
             WHERE is_active=1
            """
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "section_id": row["section_id"],
                    "name": row["name"],
                    "comment": row["comment"],
                    "frozen_until": datetime.fromisoformat(row["frozen_until"]) if row["frozen_until"] else None,
                    "frozen_is_up": (bool(row["frozen_is_up"]) if row["frozen_is_up"] is not None else None),
                    "frozen_at": datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def freeze_sensor(
    uuid: str,
    *,
    frozen_until: datetime,
    frozen_is_up: bool,
    frozen_at: datetime | None = None,
) -> bool:
    """Заморозити сенсор до конкретного часу.

    Важливо: це лише впливає на трактування online/offline у моніторингу світла
    (щоб не ловити фейкові "down" при прошивці/перезапуску сенсора).
    """
    if not uuid:
        return False
    if frozen_at is None:
        frozen_at = datetime.now()

    until_iso = frozen_until.isoformat()
    at_iso = frozen_at.isoformat()
    is_up_int = 1 if frozen_is_up else 0

    async def _op() -> bool:
        async with open_db() as db:
            cur = await db.execute(
                """
                UPDATE sensors
                   SET frozen_until=?,
                       frozen_is_up=?,
                       frozen_at=?
                 WHERE uuid=? AND is_active=1
                """,
                (until_iso, is_up_int, at_iso, uuid),
            )
            await db.commit()
            return cur.rowcount > 0

    return await _with_sqlite_retry(_op)


async def unfreeze_sensor(uuid: str) -> bool:
    """Зняти заморозку сенсора."""
    if not uuid:
        return False

    async def _op() -> bool:
        async with open_db() as db:
            cur = await db.execute(
                """
                UPDATE sensors
                   SET frozen_until=NULL,
                       frozen_is_up=NULL,
                       frozen_at=NULL
                 WHERE uuid=? AND is_active=1
                """,
                (uuid,),
            )
            await db.commit()
            return cur.rowcount > 0

    return await _with_sqlite_retry(_op)


async def deactivate_sensor(uuid: str):
    """Деактивувати сенсор."""
    async def _op() -> None:
        async with open_db() as db:
            async with db.execute(
                "SELECT building_id, is_active FROM sensors WHERE uuid=?",
                (uuid,),
            ) as cur:
                row = await cur.fetchone()
            await db.execute("UPDATE sensors SET is_active=0 WHERE uuid=?", (uuid,))
            if row and bool(row[1]) and row[0] is not None:
                await _sync_building_sensor_stats_in_tx(db, {int(row[0])})
            await db.commit()

    await _with_sqlite_retry(_op)


async def get_sensors_count_by_building(building_id: int) -> int:
    """Отримати кількість активних сенсорів будинку."""
    async with open_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM sensors WHERE building_id=? AND is_active=1",
            (building_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============ Стан електропостачання будинків ============

async def get_building_power_state(building_id: int) -> dict | None:
    """Отримати стан електропостачання будинку."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT building_id, is_up, last_change FROM building_power_state WHERE building_id=?",
            (building_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "building_id": row["building_id"],
                    "is_up": bool(row["is_up"]),
                    "last_change": datetime.fromisoformat(row["last_change"]) if row["last_change"] else None,
                }
            return None


async def set_building_power_state(building_id: int, is_up: bool) -> bool:
    """
    Встановити стан електропостачання будинку.
    Повертає True якщо стан змінився, False якщо залишився тим самим.
    """
    async def _op() -> bool:
        async with open_db() as db:
            now = datetime.now().isoformat()

            # Отримуємо поточний стан
            async with db.execute(
                "SELECT is_up FROM building_power_state WHERE building_id=?",
                (building_id,)
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                # Створюємо новий запис
                await db.execute(
                    "INSERT INTO building_power_state(building_id, is_up, last_change) VALUES(?, ?, ?)",
                    (building_id, 1 if is_up else 0, now)
                )
                await db.commit()
                return True

            current_is_up = bool(row[0])
            if current_is_up == is_up:
                return False  # Стан не змінився

            # Оновлюємо стан
            await db.execute(
                "UPDATE building_power_state SET is_up=?, last_change=? WHERE building_id=?",
                (1 if is_up else 0, now, building_id)
            )
            await db.commit()
            return True

    return await _with_sqlite_retry(_op)


async def get_all_buildings_power_state() -> dict[int, dict]:
    """Отримати стан електропостачання всіх будинків."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT building_id, is_up, last_change FROM building_power_state") as cur:
            rows = await cur.fetchall()
            return {
                row["building_id"]: {
                    "is_up": bool(row["is_up"]),
                    "last_change": datetime.fromisoformat(row["last_change"]) if row["last_change"] else None,
                }
                for row in rows
            }


# ============ Стан електропостачання секцій ============

async def get_building_section_power_state(building_id: int, section_id: int) -> dict | None:
    """Отримати стан електропостачання конкретної секції."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT building_id, section_id, is_up, last_change
              FROM building_section_power_state
             WHERE building_id=? AND section_id=?
            """,
            (building_id, section_id),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "building_id": row["building_id"],
                "section_id": row["section_id"],
                "is_up": bool(row["is_up"]),
                "last_change": datetime.fromisoformat(row["last_change"]) if row["last_change"] else None,
            }


async def set_building_section_power_state(building_id: int, section_id: int, is_up: bool) -> bool:
    """
    Встановити стан електропостачання секції.
    Повертає True якщо стан змінився (або створено новий запис).
    """
    async def _op() -> bool:
        async with open_db() as db:
            now = datetime.now().isoformat()
            async with db.execute(
                """
                SELECT is_up
                  FROM building_section_power_state
                 WHERE building_id=? AND section_id=?
                """,
                (building_id, section_id),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                await db.execute(
                    """
                    INSERT INTO building_section_power_state(building_id, section_id, is_up, last_change)
                    VALUES(?, ?, ?, ?)
                    """,
                    (building_id, section_id, 1 if is_up else 0, now),
                )
                await db.commit()
                return True

            current_is_up = bool(row[0])
            if current_is_up == is_up:
                return False

            await db.execute(
                """
                UPDATE building_section_power_state
                   SET is_up=?, last_change=?
                 WHERE building_id=? AND section_id=?
                """,
                (1 if is_up else 0, now, building_id, section_id),
            )
            await db.commit()
            return True

    return await _with_sqlite_retry(_op)


async def get_all_building_sections_power_state() -> dict[tuple[int, int], dict]:
    """Отримати стан електропостачання всіх секцій."""
    async with open_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT building_id, section_id, is_up, last_change FROM building_section_power_state"
        ) as cur:
            rows = await cur.fetchall()
            return {
                (row["building_id"], row["section_id"]): {
                    "is_up": bool(row["is_up"]),
                    "last_change": datetime.fromisoformat(row["last_change"]) if row["last_change"] else None,
                }
                for row in rows
            }
