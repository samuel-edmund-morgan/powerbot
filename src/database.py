from datetime import datetime
import re

import aiosqlite

from config import DB_PATH


# Список всіх будинків ЖК "Нова Англія"
BUILDINGS = [
    {"id": 1, "name": "Ньюкасл", "address": "24-в", "has_sensor": True},
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
    merged = " ".join(merged.split()).strip().lower()
    return merged


def tokenize_query(query: str) -> list[str]:
    """Отримати токени з пошукового запиту, прибравши розділові знаки."""
    tokens = re.findall(r"[\wа-яіїєґ’'-]+", query.lower())
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
    async with aiosqlite.connect(DB_PATH) as db:
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
        # Таблиця історії подій (up/down)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL
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
        
        # Міграція: додати колонку building_id до таблиць голосування
        try:
            await db.execute("ALTER TABLE heating_votes ADD COLUMN building_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE water_votes ADD COLUMN building_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        
        # === НОВА ТАБЛИЦЯ: Сенсори ESP32 ===
        await db.execute(
            """CREATE TABLE IF NOT EXISTS sensors (
                uuid TEXT PRIMARY KEY,
                building_id INTEGER NOT NULL,
                name TEXT,
                last_heartbeat TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (building_id) REFERENCES buildings(id)
            )"""
        )
        
        # Таблиця для стану будинків (світло є/немає)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS building_power_state (
                building_id INTEGER PRIMARY KEY,
                is_up INTEGER DEFAULT 1,
                last_change TEXT,
                FOREIGN KEY (building_id) REFERENCES buildings(id)
            )"""
        )
        
        await db.commit()

    # Після міграцій перебудовуємо keywords для всіх закладів
    await refresh_places_keywords()


async def db_set(k: str, v: str):
    """Зберегти значення за ключем."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        await db.commit()


async def db_get(k: str) -> str | None:
    """Отримати значення за ключем."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def add_subscriber(
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None
):
    """
    Додати підписника з інформацією про користувача.
    Якщо підписник вже існує — оновлює інформацію.
    """
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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


# ============ Функції для роботи з будинками ============

async def get_subscriber_building(chat_id: int) -> int | None:
    """Отримати ID будинку, на який підписаний користувач."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT building_id FROM subscribers WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_subscriber_building(chat_id: int, building_id: int) -> bool:
    """Встановити будинок для підписника. Повертає True якщо успішно."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "UPDATE subscribers SET building_id = ? WHERE chat_id = ?",
            (building_id, chat_id)
        )
        await db.commit()
        return result.rowcount > 0


async def get_building_info(building_id: int) -> dict | None:
    """Отримати інформацію про будинок з БД."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
                    "sensor_count": r[4]
                }
                for r in rows
            ]


async def get_subscribers_by_building(building_id: int = None) -> list[int] | dict[int, int]:
    """
    Отримати підписників.
    Якщо building_id вказано - повертає list[chat_id] для цього будинку.
    Якщо building_id=None - повертає dict{building_id: count} статистику по всіх будинках.
    """
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
        await db.commit()


async def list_subscribers_full() -> list[dict]:
    """
    Отримати повну інформацію про всіх підписників.
    Повертає список словників з полями:
    chat_id, username, first_name, subscribed_at, quiet_start, quiet_end
    """
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id FROM subscribers") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


async def count_subscribers() -> int:
    """Підрахувати кількість підписників."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM subscribers") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============ Тихі години ============

async def set_quiet_hours(chat_id: int, start_hour: int | None, end_hour: int | None):
    """
    Встановити тихі години для користувача.
    start_hour, end_hour: години (0-23), або None для вимкнення.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscribers SET quiet_start=?, quiet_end=? WHERE chat_id=?",
            (start_hour, end_hour, chat_id),
        )
        await db.commit()


async def get_quiet_hours(chat_id: int) -> tuple[int | None, int | None]:
    """
    Отримати тихі години для користувача.
    Повертає (start_hour, end_hour) або (None, None).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quiet_start, quiet_end FROM subscribers WHERE chat_id=?",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1]
            return None, None


async def get_subscribers_for_notification(current_hour: int) -> list[int]:
    """
    Отримати список підписників, яким можна надсилати сповіщення зараз.
    Враховує тихі години кожного користувача.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT chat_id, quiet_start, quiet_end FROM subscribers"
        ) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end in rows:
        if quiet_start is None or quiet_end is None:
            # Тихі години не налаштовані
            result.append(chat_id)
        elif quiet_start <= quiet_end:
            # Звичайний діапазон (напр. 23:00 - 07:00 НЕ працює тут)
            # Це для діапазону типу 09:00 - 18:00
            if not (quiet_start <= current_hour < quiet_end):
                result.append(chat_id)
        else:
            # Нічний діапазон (напр. 23:00 - 07:00)
            if not (current_hour >= quiet_start or current_hour < quiet_end):
                result.append(chat_id)
    
    return result


# ============ Налаштування сповіщень ============

async def set_light_notifications(chat_id: int, enabled: bool):
    """Увімкнути/вимкнути сповіщення про світло."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscribers SET light_notifications=? WHERE chat_id=?",
            (1 if enabled else 0, chat_id),
        )
        await db.commit()


async def set_alert_notifications(chat_id: int, enabled: bool):
    """Увімкнути/вимкнути сповіщення про тривоги."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscribers SET alert_notifications=? WHERE chat_id=?",
            (1 if enabled else 0, chat_id),
        )
        await db.commit()


async def get_notification_settings(chat_id: int) -> dict:
    """
    Отримати налаштування сповіщень для користувача.
    Повертає словник з ключами: light_notifications, alert_notifications, quiet_start, quiet_end
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT light_notifications, alert_notifications, quiet_start, quiet_end 
               FROM subscribers WHERE chat_id=?""",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "light_notifications": bool(row[0]) if row[0] is not None else True,
                    "alert_notifications": bool(row[1]) if row[1] is not None else True,
                    "quiet_start": row[2],
                    "quiet_end": row[3],
                }
            return {
                "light_notifications": True,
                "alert_notifications": True,
                "quiet_start": None,
                "quiet_end": None,
            }


async def get_subscribers_for_light_notification(current_hour: int, building_id: int | None = None) -> list[int]:
    """
    Отримати список підписників для сповіщень про світло.
    Враховує тихі години, налаштування light_notifications та будинок.
    
    Args:
        current_hour: поточна година для перевірки тихих годин
        building_id: ID будинку для фільтрації (якщо None - повертає для Ньюкасла)
    """
    # Якщо building_id не вказано - використовуємо Ньюкасл (поточна реалізація)
    if building_id is None:
        building_id = NEWCASTLE_BUILDING_ID
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT chat_id, quiet_start, quiet_end, light_notifications 
               FROM subscribers 
               WHERE (light_notifications = 1 OR light_notifications IS NULL)
               AND building_id = ?""",
            (building_id,)
        ) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end, _ in rows:
        if quiet_start is None or quiet_end is None:
            result.append(chat_id)
        elif quiet_start <= quiet_end:
            if not (quiet_start <= current_hour < quiet_end):
                result.append(chat_id)
        else:
            if not (current_hour >= quiet_start or current_hour < quiet_end):
                result.append(chat_id)
    
    return result


async def get_subscribers_for_alert_notification(current_hour: int) -> list[int]:
    """
    Отримати список підписників для сповіщень про тривоги.
    Враховує тихі години та налаштування alert_notifications.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT chat_id, quiet_start, quiet_end, alert_notifications 
               FROM subscribers WHERE alert_notifications = 1 OR alert_notifications IS NULL"""
        ) as cur:
            rows = await cur.fetchall()
    
    result = []
    for chat_id, quiet_start, quiet_end, _ in rows:
        if quiet_start is None or quiet_end is None:
            result.append(chat_id)
        elif quiet_start <= quiet_end:
            if not (quiet_start <= current_hour < quiet_end):
                result.append(chat_id)
        else:
            if not (current_hour >= quiet_start or current_hour < quiet_end):
                result.append(chat_id)
    
    return result


# ============ Історія подій ============

async def add_event(event_type: str) -> datetime:
    """
    Додати подію до історії.
    event_type: 'up' або 'down'
    Повертає timestamp події.
    """
    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO events (event_type, timestamp) VALUES (?, ?)",
            (event_type, now.isoformat()),
        )
        await db.commit()
    return now


async def get_last_event(event_type: str | None = None) -> tuple[str, datetime] | None:
    """
    Отримати останню подію.
    event_type: 'up', 'down' або None (будь-яка)
    Повертає (event_type, timestamp) або None.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if event_type:
            query = "SELECT event_type, timestamp FROM events WHERE event_type=? ORDER BY id DESC LIMIT 1"
            params = (event_type,)
        else:
            query = "SELECT event_type, timestamp FROM events ORDER BY id DESC LIMIT 1"
            params = ()
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], datetime.fromisoformat(row[1])
            return None


async def get_events_since(since: datetime) -> list[tuple[str, datetime]]:
    """Отримати всі події після вказаного часу."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT event_type, timestamp FROM events WHERE timestamp >= ? ORDER BY timestamp",
            (since.isoformat(),),
        ) as cur:
            rows = await cur.fetchall()
            return [(r[0], datetime.fromisoformat(r[1])) for r in rows]


async def get_all_events() -> list[tuple[str, datetime]]:
    """Отримати всі події."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT event_type, timestamp FROM events ORDER BY timestamp"
        ) as cur:
            rows = await cur.fetchall()
            return [(r[0], datetime.fromisoformat(r[1])) for r in rows]


# ============ Функції для категорій послуг ============

async def add_general_service(name: str) -> int:
    """Додати категорію послуг. Повертає ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO general_services(name) VALUES(?)",
            (name,)
        )
        await db.commit()
        return cursor.lastrowid


async def edit_general_service(service_id: int, name: str) -> bool:
    """Редагувати назву категорії. Повертає True якщо успішно."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE general_services SET name=? WHERE id=?",
            (name, service_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_general_service(service_id: int) -> bool:
    """Видалити категорію. Повертає True якщо успішно."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Спочатку видаляємо всі заклади цієї категорії
        await db.execute("DELETE FROM places WHERE service_id=?", (service_id,))
        cursor = await db.execute(
            "DELETE FROM general_services WHERE id=?",
            (service_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_all_general_services() -> list[dict]:
    """Отримати всі категорії послуг."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name FROM general_services ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]


async def get_general_service(service_id: int) -> dict | None:
    """Отримати категорію за ID."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO places(service_id, name, description, address, keywords) VALUES(?, ?, ?, ?, ?)",
            (service_id, name, description, address, merged_keywords)
        )
        await db.commit()
        return cursor.lastrowid


async def edit_place(place_id: int, service_id: int, name: str, description: str, address: str, keywords: str = None) -> bool:
    """Редагувати заклад. Повертає True якщо успішно."""
    merged_keywords = build_keywords(name, description, keywords)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE places SET service_id=?, name=?, description=?, address=?, keywords=? WHERE id=?",
            (service_id, name, description, address, merged_keywords, place_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def refresh_places_keywords() -> None:
    """Перебудувати keywords для всіх закладів (name + description + keywords)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, description, keywords FROM places") as cur:
            rows = await cur.fetchall()
        for row in rows:
            place_id, name, description, keywords = row
            merged = build_keywords(name, description, keywords)
            await db.execute("UPDATE places SET keywords=? WHERE id=?", (merged, place_id))
        await db.commit()


async def update_place_keywords(place_id: int, keywords: str) -> bool:
    """Оновити тільки ключові слова закладу."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE places SET keywords=? WHERE id=?",
            (keywords, place_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_place(place_id: int) -> bool:
    """Видалити заклад. Повертає True якщо успішно."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM places WHERE id=?",
            (place_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_places_by_service(service_id: int) -> list[dict]:
    """Отримати всі заклади певної категорії."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, service_id, name, description, address, keywords FROM places WHERE service_id=? ORDER BY name",
            (service_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], "address": r[4], "keywords": r[5]}
                for r in rows
            ]


async def get_all_places() -> list[dict]:
    """Отримати всі заклади."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords, s.name as service_name
               FROM places p
               JOIN general_services s ON p.service_id = s.id
               ORDER BY s.name, p.name"""
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], "address": r[4], "keywords": r[5], "service_name": r[6]}
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
            "(LOWER(p.name) LIKE ? OR LOWER(p.description) LIKE ? "
            "OR LOWER(p.address) LIKE ? OR LOWER(COALESCE(p.keywords, '')) LIKE ?)"
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
                 WHERE {where_clause}
                 ORDER BY match_score DESC, likes_count DESC, p.name
                 LIMIT 20"""

    # Параметри для match_score дублюють ті ж самі placeholders
    score_params = params.copy()
    all_params = params + score_params

    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, service_id, name, description, address, keywords FROM places WHERE id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"id": row[0], "service_id": row[1], "name": row[2], "description": row[3], "address": row[4], "keywords": row[5]}
            return None


# ============ Функції для лайків закладів ============

async def like_place(place_id: int, chat_id: int) -> bool:
    """Поставити лайк закладу. Повертає True якщо лайк додано, False якщо вже був."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
                (place_id, chat_id, now)
            )
            await db.commit()
            return True
        except Exception:
            return False


async def unlike_place(place_id: int, chat_id: int) -> bool:
    """Забрати лайк із закладу. Повертає True якщо лайк видалено."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM place_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def has_liked_place(place_id: int, chat_id: int) -> bool:
    """Перевірити чи користувач вже лайкнув заклад."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM place_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        ) as cur:
            return await cur.fetchone() is not None


async def get_place_likes_count(place_id: int) -> int:
    """Отримати кількість лайків закладу."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM place_likes WHERE place_id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_places_by_service_with_likes(service_id: int) -> list[dict]:
    """Отримати заклади категорії з кількістю лайків, відсортовані за лайками."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                      COALESCE(l.likes_count, 0) as likes_count
               FROM places p
               LEFT JOIN (
                   SELECT place_id, COUNT(*) as likes_count 
                   FROM place_likes 
                   GROUP BY place_id
               ) l ON p.id = l.place_id
               WHERE p.service_id = ?
               ORDER BY likes_count DESC, p.name ASC""",
            (service_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {"id": r[0], "service_id": r[1], "name": r[2], "description": r[3], 
                 "address": r[4], "keywords": r[5], "likes_count": r[6]}
                for r in rows
            ]


# ============ Функції для укриттів ============

async def get_all_shelter_places() -> list[dict]:
    """Отримати всі укриття."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO shelter_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
                (place_id, chat_id, now)
            )
            await db.commit()
            return True
        except Exception:
            return False


async def unlike_shelter(place_id: int, chat_id: int) -> bool:
    """Забрати лайк із укриття. Повертає True якщо лайк видалено."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM shelter_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def has_liked_shelter(place_id: int, chat_id: int) -> bool:
    """Перевірити чи користувач вже лайкнув укриття."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM shelter_likes WHERE place_id=? AND chat_id=?",
            (place_id, chat_id)
        ) as cur:
            return await cur.fetchone() is not None


async def get_shelter_likes_count(place_id: int) -> int:
    """Отримати кількість лайків укриття."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM shelter_likes WHERE place_id=?",
            (place_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============ Функції для голосування за опалення/воду ============

async def vote_heating(chat_id: int, has_heating: bool, building_id: int | None = None):
    """
    Проголосувати за стан опалення.
    Голос прив'язується до будинку користувача.
    """
    # Якщо building_id не вказано - отримуємо з профілю користувача
    if building_id is None:
        building_id = await get_subscriber_building(chat_id)
    
    if building_id is None:
        # Користувач не обрав будинок - не можемо зберегти голос
        return
    
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO heating_votes(chat_id, has_heating, voted_at, building_id) VALUES(?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET has_heating=excluded.has_heating, voted_at=excluded.voted_at, building_id=excluded.building_id""",
            (chat_id, 1 if has_heating else 0, now, building_id)
        )
        await db.commit()


async def vote_water(chat_id: int, has_water: bool, building_id: int | None = None):
    """
    Проголосувати за стан води.
    Голос прив'язується до будинку користувача.
    """
    # Якщо building_id не вказано - отримуємо з профілю користувача
    if building_id is None:
        building_id = await get_subscriber_building(chat_id)
    
    if building_id is None:
        # Користувач не обрав будинок - не можемо зберегти голос
        return
    
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO water_votes(chat_id, has_water, voted_at, building_id) VALUES(?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET has_water=excluded.has_water, voted_at=excluded.voted_at, building_id=excluded.building_id""",
            (chat_id, 1 if has_water else 0, now, building_id)
        )
        await db.commit()


async def get_heating_stats(building_id: int | None = None) -> dict:
    """
    Отримати статистику голосування за опалення.
    
    Args:
        building_id: ID будинку для фільтрації (якщо None - всі голоси)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if building_id is not None:
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


async def get_water_stats(building_id: int | None = None) -> dict:
    """
    Отримати статистику голосування за воду.
    
    Args:
        building_id: ID будинку для фільтрації (якщо None - всі голоси)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if building_id is not None:
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


async def get_user_vote(chat_id: int, vote_type: str) -> bool | None:
    """Отримати голос користувача (heating або water). Повертає None якщо не голосував."""
    table = "heating_votes" if vote_type == "heating" else "water_votes"
    column = "has_heating" if vote_type == "heating" else "has_water"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT {column} FROM {table} WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return row[0] == 1
            return None


async def reset_votes(building_id: int | None = None):
    """
    Скинути голоси (викликається при зміні стану світла).
    
    Args:
        building_id: ID будинку для скидання (якщо None - скидаємо всі голоси)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if building_id is not None:
            await db.execute("DELETE FROM heating_votes WHERE building_id = ?", (building_id,))
            await db.execute("DELETE FROM water_votes WHERE building_id = ?", (building_id,))
        else:
            await db.execute("DELETE FROM heating_votes")
            await db.execute("DELETE FROM water_votes")
        await db.commit()


# ============ Активні сповіщення для оновлення ============

async def save_notification(chat_id: int, message_id: int, notification_type: str = "power_change"):
    """Зберегти сповіщення для подальшого оновлення."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO active_notifications(chat_id, message_id, created_at, notification_type) VALUES(?, ?, ?, ?)",
            (chat_id, message_id, now, notification_type)
        )
        await db.commit()


async def get_active_notifications() -> list[dict]:
    """Отримати всі активні сповіщення для оновлення."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, chat_id, message_id, created_at FROM active_notifications") as cur:
            rows = await cur.fetchall()
            return [{"id": r["id"], "chat_id": r["chat_id"], "message_id": r["message_id"], "created_at": r["created_at"]} for r in rows]


async def delete_notification(notification_id: int):
    """Видалити сповіщення за ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM active_notifications WHERE id=?", (notification_id,))
        await db.commit()


async def clear_all_notifications():
    """Видалити всі активні сповіщення (при зміні стану світла)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM active_notifications")
        await db.commit()


# ============ Останнє повідомлення бота (чистий чат) ============

async def save_last_bot_message(chat_id: int, message_id: int):
    """Зберегти останнє повідомлення бота для чату."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO last_bot_message(chat_id, message_id) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET message_id=excluded.message_id",
            (chat_id, message_id)
        )
        await db.commit()


async def get_last_bot_message(chat_id: int) -> int | None:
    """Отримати ID останнього повідомлення бота для чату."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT message_id FROM last_bot_message WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def delete_last_bot_message_record(chat_id: int):
    """Видалити запис про останнє повідомлення."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM last_bot_message WHERE chat_id=?", (chat_id,))
        await db.commit()


# ============ Сенсори ESP32 ============

async def register_sensor(uuid: str, building_id: int, name: str | None = None) -> bool:
    """
    Реєстрація нового сенсора або оновлення існуючого.
    Повертає True якщо сенсор новий, False якщо оновлений.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        # Перевіряємо чи сенсор вже існує
        async with db.execute("SELECT uuid FROM sensors WHERE uuid=?", (uuid,)) as cur:
            exists = await cur.fetchone()
        
        if exists:
            # Оновлюємо існуючий сенсор
            await db.execute(
                "UPDATE sensors SET building_id=?, name=?, is_active=1 WHERE uuid=?",
                (building_id, name, uuid)
            )
            await db.commit()
            return False
        else:
            # Створюємо новий сенсор
            await db.execute(
                "INSERT INTO sensors(uuid, building_id, name, created_at, is_active) VALUES(?, ?, ?, ?, 1)",
                (uuid, building_id, name, now)
            )
            await db.commit()
            return True


async def update_sensor_heartbeat(uuid: str) -> bool:
    """
    Оновити час останнього heartbeat сенсора.
    Повертає True якщо сенсор знайдено, False якщо ні.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "UPDATE sensors SET last_heartbeat=? WHERE uuid=? AND is_active=1",
            (now, uuid)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_sensor_by_uuid(uuid: str) -> dict | None:
    """Отримати сенсор за UUID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT uuid, building_id, name, last_heartbeat, created_at, is_active FROM sensors WHERE uuid=?",
            (uuid,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "name": row["name"],
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                    "is_active": bool(row["is_active"]),
                }
            return None


async def get_sensors_by_building(building_id: int) -> list[dict]:
    """Отримати всі активні сенсори будинку."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT uuid, building_id, name, last_heartbeat, created_at FROM sensors WHERE building_id=? AND is_active=1",
            (building_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "name": row["name"],
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def get_all_active_sensors() -> list[dict]:
    """Отримати всі активні сенсори."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT uuid, building_id, name, last_heartbeat, created_at FROM sensors WHERE is_active=1"
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "uuid": row["uuid"],
                    "building_id": row["building_id"],
                    "name": row["name"],
                    "last_heartbeat": datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
                    "created_at": datetime.fromisoformat(row["created_at"]),
                }
                for row in rows
            ]


async def deactivate_sensor(uuid: str):
    """Деактивувати сенсор."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sensors SET is_active=0 WHERE uuid=?", (uuid,))
        await db.commit()


async def get_sensors_count_by_building(building_id: int) -> int:
    """Отримати кількість активних сенсорів будинку."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM sensors WHERE building_id=? AND is_active=1",
            (building_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============ Стан електропостачання будинків ============

async def get_building_power_state(building_id: int) -> dict | None:
    """Отримати стан електропостачання будинку."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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


async def get_all_buildings_power_state() -> dict[int, dict]:
    """Отримати стан електропостачання всіх будинків."""
    async with aiosqlite.connect(DB_PATH) as db:
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
