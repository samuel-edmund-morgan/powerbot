# 🔌 PowerBot

Telegram бот для моніторингу електропостачання житлового комплексу з підтримкою кількох будинків.

## ✨ Можливості

- 📡 **Моніторинг електропостачання** — автоматичне визначення відключень через ESP32 сенсори
- 🏠 **Підтримка кількох будинків** — кожен будинок може мати свої сенсори
- 🔔 **Push-сповіщення** — миттєві повідомлення про відключення/відновлення світла
- 🚨 **Повітряні тривоги** — сповіщення про тривоги через ukrainealarm.com та alerts.in.ua
- 🌡️ **Голосування** — опитування про воду/опалення серед мешканців
- 🗺️ **Довідник** — корисні місця поблизу (кафе, магазини, аптеки)
- 📊 **Статистика** — історія відключень та аналітика
- 🌤️ **Погода** — актуальний прогноз погоди
- 🔌 **HTTP API** — API для ESP32 сенсорів (heartbeat)

## 🏗️ Структура проєкту

```
/home/powerbot/powerbot/
├── prod/                   # Production середовище
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── handlers.py
│   ├── services.py
│   ├── api_server.py
│   ├── weather.py
│   ├── alerts.py
│   ├── maps/
│   ├── .env               # Конфігурація (не в Git!)
│   └── state.db           # База даних (не в Git!)
│
├── test/                   # Test середовище (аналогічна структура)
│   └── .env.example        # Шаблон конфігурації
│
├── scripts/                # Адмінські скрипти
│   ├── fix_keywords.py
│   └── sensor_manager.py
│
├── sensors/                # ESP32 firmware/супутні матеріали
├── docker/                 # Docker entrypoint
│   └── entrypoint.sh
├── nginx.default.conf      # Nginx конфіг для доступу по IP
├── nginx.sensors.conf      # Nginx конфіг для домену sensors.*
├── Dockerfile              # Docker image
├── docker-compose.yml      # Docker compose
├── .dockerignore           # Docker ignore
├── requirements.txt        # Python dependencies
├── deploy_code.sh          # Деплой коду test → prod
├── migrate_db.py           # Міграція БД test → prod (безпечне злиття)
├── schema.sql              # Схема бази даних
├── backup_db.sh            # Ручний бекап БД
├── .gitignore
└── README.md
```

## 🚀 Встановлення

### Вимоги

- Ubuntu 22.04+ / Debian 12+
- Python 3.11+
- SQLite 3
- systemd
- nginx

### Крок 1: Клонування репозиторію

```bash
# Створіть користувача
sudo useradd -m -s /bin/bash powerbot
sudo su - powerbot

# Клонуйте репозиторій
cd /home/powerbot
git clone https://github.com/samuel-edmund-morgan/powerbot.git
cd powerbot
```

### Крок 2: Створення віртуального середовища

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install aiogram aiosqlite python-dotenv aiohttp
```

### Крок 3: Налаштування середовищ

```bash
# Копіюємо шаблон конфігурації
cp test/.env.example prod/.env
cp test/.env.example test/.env

# Редагуємо конфігурацію (замініть на реальні значення)
nano prod/.env
nano test/.env
```

### Крок 4: Створення бази даних

```bash
# Для production
cd prod
sqlite3 state.db < ../schema.sql

# Для test
cd ../test
sqlite3 state.db < ../schema.sql
```

### Крок 5: Налаштування systemd

Створіть файл `/etc/systemd/system/bot-prod.service`:

```ini
[Unit]
Description=Telegram Power Bot - PRODUCTION
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=powerbot
WorkingDirectory=/home/powerbot/powerbot/prod
EnvironmentFile=/home/powerbot/powerbot/prod/.env
ExecStart=/home/powerbot/powerbot/.venv/bin/python /home/powerbot/powerbot/prod/main.py
Restart=always
RestartSec=3

NoNewPrivileges=true
PrivateTmp=true

ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/home/powerbot/powerbot/prod

ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
```

Аналогічно для test (замініть `prod` на `test` та Description).

```bash
sudo systemctl daemon-reload
sudo systemctl enable bot-prod.service
sudo systemctl start bot-prod.service
sudo systemctl status bot-prod.service
```

### Крок 6: Налаштування nginx

У репозиторії є готові конфіги:
- `nginx.default.conf` — доступ по IP (наприклад `http://64.181.205.211/...`)
- `nginx.sensors.conf` — домен `sensors.*`

```bash
sudo cp nginx.default.conf /etc/nginx/sites-available/default
sudo cp nginx.sensors.conf /etc/nginx/sites-available/sensors
sudo ln -sf /etc/nginx/sites-available/sensors /etc/nginx/sites-enabled/sensors
sudo nginx -t && sudo systemctl reload nginx
```

## 🐳 Docker деплой

### 1) Підготовка .env

Заповніть `prod/.env` (або `test/.env`). Мінімально потрібні:
- `BOT_TOKEN`
- `BOT_USERNAME`
- `ADMIN_IDS`
- `ALERTS_API_KEY` / `ALERTS_IN_UA_API_KEY`
- `SENSOR_API_KEY`

Згенерувати API ключ для сенсора можна так:
```bash
python scripts/sensor_manager.py token --generate
```

### 2) Запуск production контейнера (1 команда)

```bash
docker compose up -d powerbot-prod
```

Контейнер читає `prod/.env` і запускає `/app/prod/main.py`.

### 3) Запуск test контейнера (опційно)

```bash
docker compose --profile test up -d powerbot-test
```

### 4) Nginx (опційно)

Якщо потрібен reverse proxy — використайте готові `nginx.default.conf` та `nginx.sensors.conf`
з цього репозиторію (див. розділ “Налаштування nginx”).

## ⚙️ Конфігурація (.env)

```bash
# Telegram Bot Token від @BotFather
BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Username бота (без @)
BOT_USERNAME="YourBotUsername"

# ID адміністраторів (через кому)
ADMIN_IDS="123456789,987654321"

# Тег адміна для зворотного зв'язку
ADMIN_TAG="@YourAdminUsername"

# Координати для погоди (Open-Meteo)
WEATHER_LAT="50.4501"
WEATHER_LON="30.5234"
WEATHER_API_URL="https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEZONE="Europe/Kyiv"

# Телефони сервісів
SECURITY_PHONE="+380XXXXXXXXX"
PLUMBER_PHONE="+380XXXXXXXXX"
ELECTRICIAN_PHONE="+380XXXXXXXXX"
ELEVATOR_PHONES="+380XXXXXXXXX, +380XXXXXXXXX"

# API ключі для тривог
ALERTS_API_KEY="your_alerts_api_key_here"
ALERTS_IN_UA_API_KEY="your_alerts_in_ua_api_key_here"
ALERTS_CITY_ID_UKRAINEALARM="31"
ALERTS_CITY_UID_ALERTS_IN_UA="31"
ALERTS_API_URL="https://api.ukrainealarm.com/api/v3"
ALERTS_IN_UA_API_URL="https://api.alerts.in.ua/v1"
ALERTS_IN_UA_RATIO=3

# ESP32 сенсори
# Для prod: API_PORT=8081, для test: API_PORT=8082
API_PORT=8081
SENSOR_API_KEY="your-64-char-hex-key"
SENSOR_TIMEOUT_SEC=150

# Параметри масових розсилок
BROADCAST_RATE_PER_SEC=20
BROADCAST_CONCURRENCY=8
BROADCAST_MAX_RETRIES=1
```

## 🔌 Sensors API

### Heartbeat Endpoint (prod)

```bash
POST /api/v1/heartbeat
Content-Type: application/json

{
  "api_key": "your-secret-api-key",
  "building_id": 1,
  "sensor_uuid": "esp32-unique-id"
}
```

### Heartbeat Endpoint (test)

```bash
POST /api/v1/heartbeat-test
Content-Type: application/json

{
  "api_key": "your-secret-api-key",
  "building_id": 1,
  "sensor_uuid": "esp32-unique-id"
}
```

**Відповідь:**
```json
{
  "status": "ok",
  "timestamp": "2026-01-23T19:41:42.804846",
  "building": "Ньюкасл",
  "sensor_uuid": "esp32-unique-id"
}
```

### Health endpoint

- `/health` → prod (порт 8081)
- `/health-test` → test (порт 8082)

## 📦 Деплой

### Деплой коду (test → prod)

```bash
cd /home/powerbot/powerbot

# Перегляд змін (dry run)
./deploy_code.sh --dry-run

# Виконання деплою
./deploy_code.sh

# Перезапуск бота
sudo systemctl restart bot-prod.service
```

### Міграція БД (test → prod)

`migrate_db.py` додає нові таблиці/колонки та зливає статичні дані **без видалення** існуючих.
Таблиці `kv`, `sensors`, `building_power_state` не перезаписуються, щоб не затирати прод-стан.

```bash
# Зупиняємо бота
sudo systemctl stop bot-prod.service

# Перегляд змін (dry run)
python migrate_db.py --dry-run

# Виконання міграції
python migrate_db.py

# Запускаємо бота
sudo systemctl start bot-prod.service
```

## 🔧 Скрипти

```bash
# Очистка дублікатів keywords
python scripts/fix_keywords.py test --dry-run
python scripts/fix_keywords.py prod

# Менеджер сенсорів
python scripts/sensor_manager.py buildings
python scripts/sensor_manager.py list --env prod
python scripts/sensor_manager.py info 1 --env prod
python scripts/sensor_manager.py test 1 --env test
```

## 🔧 Корисні команди

```bash
# Статус бота
sudo systemctl status bot-prod.service

# Логи в реальному часі
sudo journalctl -u bot-prod.service -f

# Перезапуск
sudo systemctl restart bot-prod.service

# Ручний бекап БД
./backup_db.sh prod    # бекап production
./backup_db.sh test    # бекап test
```

## 💾 Бекапи

Бекапи зберігаються в `/home/powerbot/powerbot/backups/`:

| Тип | Директорія | Коли створюється |
|-----|------------|------------------|
| Код | `backups/code/` | Автоматично при `./deploy_code.sh` |
| БД | `backups/db/` | Автоматично при `python migrate_db.py` |
| БД | `backups/db/` | Вручну при `./backup_db.sh` |

## 🗃️ База даних

Основні таблиці:

| Таблиця | Призначення |
|---------|-------------|
| `subscribers` | Підписники бота |
| `buildings` | Будинки комплексу |
| `events` | Історія подій (up/down) |
| `sensors` | ESP32 сенсори для моніторингу |
| `building_power_state` | Стан електропостачання будинків |
| `water_votes` | Голосування за воду |
| `heating_votes` | Голосування за опалення |
| `places` | Довідник корисних місць |
| `place_likes` | Лайки місць |

## 📝 Ліцензія

MIT License

## 👨‍💻 Автор

Створено для жителів ЖК з ❤️
