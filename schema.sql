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
    schedule_notifications INTEGER DEFAULT 1, -- Сповіщення про графіки (1/0)
    building_id INTEGER DEFAULT NULL,        -- ID будинку
    section_id INTEGER DEFAULT NULL          -- Номер секції (1..3)
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
    timestamp TEXT NOT NULL,                 -- Час події (ISO 8601)
    building_id INTEGER DEFAULT NULL,        -- ID будинку
    section_id INTEGER DEFAULT NULL          -- Номер секції (1..3)
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
    is_published INTEGER NOT NULL DEFAULT 1, -- Показувати мешканцям у каталозі (1/0)
    is_verified INTEGER DEFAULT 0,           -- Verified-статус для бізнес-режиму
    verified_tier TEXT DEFAULT NULL,         -- Рівень підписки (light/pro/partner)
    verified_until TEXT DEFAULT NULL,        -- Дата завершення Verified (ISO 8601)
    business_enabled INTEGER DEFAULT 0,      -- Дозвіл на бізнес-функції (1/0)
    opening_hours TEXT DEFAULT NULL,         -- Години роботи (для verified/paid)
    contact_type TEXT DEFAULT NULL,          -- call/chat
    contact_value TEXT DEFAULT NULL,         -- телефон або @username/посилання
    link_url TEXT DEFAULT NULL,              -- 1 URL (сайт/інстаграм/меню)
    promo_code TEXT DEFAULT NULL,            -- 1 активний промокод
    FOREIGN KEY (service_id) REFERENCES general_services(id) ON DELETE CASCADE
);

-- Власники бізнес-карток (зв'язок place <-> Telegram user)
CREATE TABLE IF NOT EXISTS business_owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL,               -- FK на places
    tg_user_id INTEGER NOT NULL,             -- Telegram ID користувача
    role TEXT NOT NULL DEFAULT 'owner',      -- Роль: owner/manager
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    approved_at TEXT DEFAULT NULL,           -- Час підтвердження (ISO 8601)
    approved_by INTEGER DEFAULT NULL,        -- Telegram ID адміністратора
    UNIQUE (place_id, tg_user_id),
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Поточний стан підписки бізнесу
CREATE TABLE IF NOT EXISTS business_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL UNIQUE,        -- Одна активна картка підписки на заклад
    tier TEXT NOT NULL DEFAULT 'free',       -- free/light/pro/partner
    status TEXT NOT NULL DEFAULT 'inactive', -- inactive/active/past_due/canceled
    starts_at TEXT DEFAULT NULL,             -- Початок дії (ISO 8601)
    expires_at TEXT DEFAULT NULL,            -- Кінець дії (ISO 8601)
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    updated_at TEXT NOT NULL,                -- Час останнього оновлення (ISO 8601)
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Аудит чутливих бізнес-змін
CREATE TABLE IF NOT EXISTS business_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL,               -- FK на places
    actor_tg_user_id INTEGER DEFAULT NULL,   -- Хто виконав дію
    action TEXT NOT NULL,                    -- Тип дії
    payload_json TEXT DEFAULT NULL,          -- JSON з деталями
    created_at TEXT NOT NULL,                -- Час події (ISO 8601)
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Події оплати/білінгу (підготовка до Telegram Stars)
CREATE TABLE IF NOT EXISTS business_payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL,               -- FK на places
    provider TEXT NOT NULL DEFAULT 'telegram_stars',
    external_payment_id TEXT DEFAULT NULL,   -- ID транзакції провайдера
    event_type TEXT NOT NULL,                -- payment_succeeded/refund/etc
    amount_stars INTEGER DEFAULT NULL,       -- Сума в Stars
    currency TEXT DEFAULT 'XTR',             -- Внутрішнє позначення валюти
    status TEXT NOT NULL DEFAULT 'new',      -- new/processed/failed
    raw_payload_json TEXT DEFAULT NULL,      -- Сирі дані події
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    processed_at TEXT DEFAULT NULL,          -- Час обробки (ISO 8601)
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Одноразові claim-токени для прив'язки існуючого бізнесу
CREATE TABLE IF NOT EXISTS business_claim_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL,               -- FK на places
    token TEXT NOT NULL UNIQUE,              -- Одноразовий код claim
    status TEXT NOT NULL DEFAULT 'active',   -- active/used/expired/revoked
    attempts_left INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    expires_at TEXT NOT NULL,                -- Час завершення дії (ISO 8601)
    created_by INTEGER DEFAULT NULL,         -- Telegram ID адміна, хто згенерував
    used_at TEXT DEFAULT NULL,               -- Час використання (ISO 8601)
    used_by INTEGER DEFAULT NULL,            -- Telegram ID користувача, хто використав
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);

-- Укриття (спрощений список місць)
CREATE TABLE IF NOT EXISTS shelter_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                      -- Назва укриття
    description TEXT,                        -- Опис
    address TEXT,                            -- Маппінг на файл карти
    keywords TEXT DEFAULT NULL               -- Ключові слова (опційно)
);

-- Голосування за опалення
CREATE TABLE IF NOT EXISTS heating_votes (
    chat_id INTEGER PRIMARY KEY,
    has_heating INTEGER NOT NULL,            -- Є опалення (1/0)
    voted_at TEXT NOT NULL,                  -- Час голосування (ISO 8601)
    building_id INTEGER DEFAULT NULL,        -- ID будинку
    section_id INTEGER DEFAULT NULL          -- Номер секції (1..3)
);

-- Голосування за воду
CREATE TABLE IF NOT EXISTS water_votes (
    chat_id INTEGER PRIMARY KEY,
    has_water INTEGER NOT NULL,              -- Є вода (1/0)
    voted_at TEXT NOT NULL,                  -- Час голосування (ISO 8601)
    building_id INTEGER DEFAULT NULL,        -- ID будинку
    section_id INTEGER DEFAULT NULL          -- Номер секції (1..3)
);

-- Активні сповіщення (для видалення старих)
CREATE TABLE IF NOT EXISTS active_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,                -- Час створення (ISO 8601)
    notification_type TEXT DEFAULT 'power_change'  -- Тип сповіщення
);

-- Кеш стану графіків ЯСНО (для виявлення змін)
CREATE TABLE IF NOT EXISTS yasno_schedule_state (
    building_id INTEGER NOT NULL,
    queue_key TEXT NOT NULL,
    day_key TEXT NOT NULL,
    status TEXT DEFAULT NULL,
    slots_hash TEXT DEFAULT NULL,
    updated_at TEXT DEFAULT NULL,
    PRIMARY KEY (building_id, queue_key, day_key)
);

-- Кеш стану графіків ЯСНО по секціях (v2)
CREATE TABLE IF NOT EXISTS yasno_schedule_state_v2 (
    building_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL,
    queue_key TEXT NOT NULL,
    day_key TEXT NOT NULL,
    status TEXT DEFAULT NULL,
    slots_hash TEXT DEFAULT NULL,
    updated_at TEXT DEFAULT NULL,
    PRIMARY KEY (building_id, section_id, queue_key, day_key)
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

-- Перегляди карток закладів (агрегація по днях)
-- day = локальна дата (YYYY-MM-DD), щоб зручно рахувати за "останні 30 днів".
CREATE TABLE IF NOT EXISTS place_views_daily (
    place_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (place_id, day),
    FOREIGN KEY (place_id) REFERENCES places(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_place_views_daily_day ON place_views_daily (day);

-- Лайки укриттів
CREATE TABLE IF NOT EXISTS shelter_likes (
    place_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    liked_at TEXT NOT NULL,                  -- Час лайку (ISO 8601)
    PRIMARY KEY (place_id, chat_id),
    FOREIGN KEY (place_id) REFERENCES shelter_places(id) ON DELETE CASCADE
);

-- Сенсори (ESP32 heartbeat датчики)
CREATE TABLE IF NOT EXISTS sensors (
    uuid TEXT PRIMARY KEY,                   -- Унікальний ідентифікатор сенсора
    building_id INTEGER NOT NULL,            -- FK на buildings
    section_id INTEGER DEFAULT NULL,         -- Номер секції (1..3)
    name TEXT,                               -- Назва сенсора (опціонально)
    comment TEXT DEFAULT NULL,               -- Опціональна примітка (квартира/контакт)
    frozen_until TEXT DEFAULT NULL,          -- Заморозка сенсора до (ISO 8601), щоб не ловити фейкові "down" під час прошивки
    frozen_is_up INTEGER DEFAULT NULL,       -- Поки заморожений: внесок у стан секції (1=UP, 0=DOWN)
    frozen_at TEXT DEFAULT NULL,             -- Коли заморожено (ISO 8601)
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

-- Стан електропостачання по секціях (building_id + section_id)
CREATE TABLE IF NOT EXISTS building_section_power_state (
    building_id INTEGER NOT NULL,            -- FK на buildings
    section_id INTEGER NOT NULL,             -- Номер секції (1..3)
    is_up INTEGER DEFAULT 1,                 -- Є світло (1/0)
    last_change TEXT,                        -- Час останньої зміни (ISO 8601)
    PRIMARY KEY (building_id, section_id),
    FOREIGN KEY (building_id) REFERENCES buildings(id)
);

-- Черга адмін-задач (control-plane): tasks executed by main bot (data-plane)
CREATE TABLE IF NOT EXISTS admin_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                      -- broadcast | light_notify | ...
    payload_json TEXT NOT NULL,              -- JSON payload
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|canceled
    created_at TEXT NOT NULL,                -- ISO 8601
    created_by INTEGER DEFAULT NULL,         -- tg user id (admin)
    started_at TEXT DEFAULT NULL,            -- ISO 8601
    finished_at TEXT DEFAULT NULL,           -- ISO 8601
    updated_at TEXT DEFAULT NULL,            -- ISO 8601 (heartbeat/progress)
    attempts INTEGER NOT NULL DEFAULT 0,
    progress_current INTEGER DEFAULT 0,
    progress_total INTEGER DEFAULT 0,
    last_error TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_jobs_status_created
    ON admin_jobs (status, created_at);

-- Індекси бізнес-режиму
CREATE INDEX IF NOT EXISTS idx_subscribers_building_section
    ON subscribers (building_id, section_id);

CREATE INDEX IF NOT EXISTS idx_sensors_building_section_active
    ON sensors (building_id, section_id, is_active);

CREATE INDEX IF NOT EXISTS idx_events_building_section_timestamp
    ON events (building_id, section_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_heating_votes_building_section
    ON heating_votes (building_id, section_id);

CREATE INDEX IF NOT EXISTS idx_water_votes_building_section
    ON water_votes (building_id, section_id);

CREATE INDEX IF NOT EXISTS idx_places_business_enabled_verified
    ON places (business_enabled, is_verified);

CREATE INDEX IF NOT EXISTS idx_places_service_published
    ON places (service_id, is_published);

CREATE INDEX IF NOT EXISTS idx_places_verified_tier
    ON places (verified_tier);

CREATE INDEX IF NOT EXISTS idx_business_owners_tg_user
    ON business_owners (tg_user_id);

CREATE INDEX IF NOT EXISTS idx_business_owners_place_status
    ON business_owners (place_id, status);

CREATE INDEX IF NOT EXISTS idx_business_subscriptions_status_expires
    ON business_subscriptions (status, expires_at);

CREATE INDEX IF NOT EXISTS idx_business_audit_place_created
    ON business_audit_log (place_id, created_at);

CREATE INDEX IF NOT EXISTS idx_business_payment_place_created
    ON business_payment_events (place_id, created_at);

CREATE INDEX IF NOT EXISTS idx_business_payment_external
    ON business_payment_events (provider, external_payment_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_payment_event
    ON business_payment_events (provider, external_payment_id, event_type);

CREATE INDEX IF NOT EXISTS idx_business_claim_token_place_status
    ON business_claim_tokens (place_id, status);

CREATE INDEX IF NOT EXISTS idx_business_claim_token_status_expires
    ON business_claim_tokens (status, expires_at);

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

-- Укриття (приклади)
INSERT OR IGNORE INTO shelter_places (id, name, description, address) VALUES
    (1, '🚗 Паркінг', 'Підземний паркінг ЖК. Відносно безпечне місце під час тривоги.', 'Паркінг'),
    (2, '📦 Комора', 'Комора для мешканців Кембріджа. Відносно безпечне місце під час тривоги.', 'Комора');
