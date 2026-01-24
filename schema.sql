-- =============================================================================
-- PowerBot Database Schema
-- =============================================================================
-- Цей файл містить схему бази даних для PowerBot.
-- Для створення нової бази виконайте:
--   sqlite3 state.db < schema.sql
-- =============================================================================

-- Таблиця підписників (користувачів бота)
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    quiet_start INTEGER DEFAULT NULL,        -- Початок тихого режиму (година)
    quiet_end INTEGER DEFAULT NULL,          -- Кінець тихого режиму (година)
    username TEXT DEFAULT NULL,              -- @username
    first_name TEXT DEFAULT NULL,            -- Ім'я користувача
    subscribed_at TEXT DEFAULT NULL,         -- Дата підписки (ISO 8601)
    light_notifications INTEGER DEFAULT 1,   -- Сповіщення про світло (1/0)
    alert_notifications INTEGER DEFAULT 1,   -- Сповіщення про тривоги (1/0)
    building_id INTEGER DEFAULT NULL         -- ID будинку
);

-- Таблиця будинків
CREATE TABLE IF NOT EXISTS buildings (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                      -- Назва будинку
    address TEXT NOT NULL,                   -- Адреса
    has_sensor INTEGER DEFAULT 0,            -- Чи є датчик (1/0)
    sensor_count INTEGER DEFAULT 0           -- Кількість датчиків
);

-- Key-Value сховище для налаштувань
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- Таблиця подій (up/down)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,                -- 'up' або 'down'
    timestamp TEXT NOT NULL                  -- Час події (ISO 8601)
);

-- Загальні категорії сервісів
CREATE TABLE IF NOT EXISTS general_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE                -- Назва категорії
);

-- Місця (магазини, кафе тощо)
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,             -- FK на general_services
    name TEXT NOT NULL,                      -- Назва місця
    description TEXT,                        -- Опис
    address TEXT,                            -- Адреса
    keywords TEXT DEFAULT NULL,              -- Ключові слова для пошуку
    FOREIGN KEY (service_id) REFERENCES general_services(id) ON DELETE CASCADE
);

-- Голосування за опалення
CREATE TABLE IF NOT EXISTS heating_votes (
    chat_id INTEGER PRIMARY KEY,
    has_heating INTEGER NOT NULL,            -- Є опалення (1/0)
    voted_at TEXT NOT NULL,                  -- Час голосування (ISO 8601)
    building_id INTEGER DEFAULT NULL         -- ID будинку
);

-- Голосування за воду
CREATE TABLE IF NOT EXISTS water_votes (
    chat_id INTEGER PRIMARY KEY,
    has_water INTEGER NOT NULL,              -- Є вода (1/0)
    voted_at TEXT NOT NULL,                  -- Час голосування (ISO 8601)
    building_id INTEGER DEFAULT NULL         -- ID будинку
);

-- Активні сповіщення (для видалення старих)
CREATE TABLE IF NOT EXISTS active_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    notification_type TEXT DEFAULT 'power_change'  -- Тип сповіщення
);

-- Останнє повідомлення бота користувачу
CREATE TABLE IF NOT EXISTS last_bot_message (
    chat_id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL
);

-- Лайки місць
CREATE TABLE IF NOT EXISTS place_likes (
    place_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    liked_at TEXT NOT NULL,                  -- Час лайку (ISO 8601)
    PRIMARY KEY (place_id, chat_id),
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Сенсори (ESP32 heartbeat датчики)
CREATE TABLE IF NOT EXISTS sensors (
    uuid TEXT PRIMARY KEY,                   -- Унікальний ідентифікатор сенсора
    building_id INTEGER NOT NULL,            -- FK на buildings
    name TEXT,                               -- Назва сенсора (опціонально)
    last_heartbeat TEXT,                     -- Час останнього heartbeat (ISO 8601)
    created_at TEXT NOT NULL,                -- Час реєстрації (ISO 8601)
    is_active INTEGER DEFAULT 1,             -- Активний (1/0)
    FOREIGN KEY (building_id) REFERENCES buildings(id)
);

-- Стан електропостачання будинків
CREATE TABLE IF NOT EXISTS building_power_state (
    building_id INTEGER PRIMARY KEY,         -- FK на buildings
    is_up INTEGER DEFAULT 1,                 -- Є світло (1/0)
    last_change TEXT,                        -- Час останньої зміни (ISO 8601)
    FOREIGN KEY (building_id) REFERENCES buildings(id)
);

-- =============================================================================
-- Початкові дані (приклад - замініть на реальні)
-- =============================================================================

-- Будинки (актуальний перелік ЖК "Нова Англія")
INSERT OR IGNORE INTO buildings (id, name, address, has_sensor, sensor_count) VALUES
    (1, 'Ньюкасл', '24-в', 1, 1),
    (2, 'Оксфорд', '28-б', 0, 0),
    (3, 'Кембрідж', '26', 0, 0),
    (4, 'Ліверпуль', '24-а', 0, 0),
    (5, 'Брістоль', '24-б', 0, 0),
    (6, 'Бермінгем', '26-б', 0, 0),
    (7, 'Честер', '28-д', 0, 0),
    (8, 'Манчестер', '26-г', 0, 0),
    (9, 'Брайтон', '26-в', 0, 0),
    (10, 'Лондон', '28-е', 0, 0),
    (11, 'Лінкольн', '28-к', 0, 0),
    (12, 'Віндзор', '26-д', 0, 0),
    (13, 'Ноттінгем', '24-г', 0, 0),
    (14, 'Престон', '-', 0, 0);

-- Категорії сервісів
INSERT OR IGNORE INTO general_services (name) VALUES
    ('Кафе та ресторани'),
    ('Магазини'),
    ('Аптеки'),
    ('Банки'),
    ('Медицина'),
    ('Краса'),
    ('Спорт'),
    ('Розваги'),
    ('Освіта'),
    ('Послуги');
