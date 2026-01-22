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

-- Таблиця подій (світло вимкнено/увімкнено)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,                -- 'power_off', 'power_on'
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

-- =============================================================================
-- Початкові дані (приклад - замініть на реальні)
-- =============================================================================

-- Будинки
INSERT OR IGNORE INTO buildings (id, name, address, has_sensor, sensor_count) VALUES
    (1, 'Newcastle', 'вул. Прикладу 1', 1, 15),
    (2, 'Brighton', 'вул. Прикладу 2', 0, 0);

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
