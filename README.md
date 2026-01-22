# 🔌 PowerBot

Telegram бот для моніторингу електропостачання житлового комплексу з підтримкою кількох будинків.

## ✨ Можливості

- 📡 **Моніторинг електропостачання** — автоматичне визначення відключень через пінг датчиків
- 🏠 **Підтримка кількох будинків** — кожен будинок може мати свій датчик
- 🔔 **Push-сповіщення** — миттєві повідомлення про відключення/відновлення світла
- 🚨 **Повітряні тривоги** — сповіщення про тривоги через ukrainealarm.com API
- 🌡️ **Голосування** — опитування про воду/опалення серед мешканців
- 🗺️ **Довідник** — корисні місця поблизу (кафе, магазини, аптеки)
- 📊 **Статистика** — історія відключень та аналітика
- 🌤️ **Погода** — актуальний прогноз погоди

## 🏗️ Структура проєкту

```
/home/powerbot/powerbot/
├── prod/                   # Production середовище
│   ├── main.py            # Точка входу
│   ├── config.py          # Конфігурація з .env
│   ├── database.py        # Робота з SQLite
│   ├── handlers.py        # Обробники Telegram команд
│   ├── services.py        # Бізнес-логіка
│   ├── weather.py         # API погоди
│   ├── alerts.py          # API тривог
│   ├── maps/              # Зображення карт
│   ├── .env               # Конфігурація (не в Git!)
│   └── state.db           # База даних (не в Git!)
│
├── test/                   # Test середовище (аналогічна структура)
│
├── deploy_code.sh         # Деплой коду test → prod
├── migrate_db.py          # Міграція БД test → prod
├── schema.sql             # Схема бази даних
├── .env.example           # Шаблон конфігурації
├── .gitignore             # Ігноровані файли
└── README.md              # Цей файл
```

## 🚀 Встановлення

### Вимоги

- Ubuntu 22.04+ / Debian 12+
- Python 3.11+
- SQLite 3
- systemd

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
cp .env.example prod/.env
cp .env.example test/.env

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
After=network.target

[Service]
Type=simple
User=powerbot
Group=powerbot
WorkingDirectory=/home/powerbot/powerbot/prod
ExecStart=/home/powerbot/powerbot/.venv/bin/python /home/powerbot/powerbot/prod/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Аналогічно для test (змініть `prod` на `test` та Description).

```bash
# Активуємо та запускаємо
sudo systemctl daemon-reload
sudo systemctl enable bot-prod.service
sudo systemctl start bot-prod.service

# Перевіряємо статус
sudo systemctl status bot-prod.service
```

## ⚙️ Конфігурація (.env)

```bash
# Режим роботи: "prod" або "test"
BOT_MODE="prod"

# Telegram Bot Token від @BotFather
BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Username бота (без @)
BOT_USERNAME="YourBotUsername"

# ID адміністраторів (через кому)
ADMIN_IDS="123456789,987654321"

# Тег адміна для зворотного зв'язку
ADMIN_TAG="@YourAdminUsername"

# IP адреси датчиків для моніторингу (через кому)
HOME_IP="192.168.1.1,192.168.1.2"

# Налаштування моніторингу
CHECK_INTERVAL_SEC="15"          # Інтервал перевірки (сек)
FAILS_TO_DECLARE_DOWN="150"      # Кількість fail до оголошення DOWN
SUCCESSES_TO_DECLARE_UP="1"      # Кількість success до оголошення UP
TIMEOUT_SEC="1"                  # Таймаут пінгу (сек)
DOWN_THRESHOLD="0.6"             # Поріг недоступності (0.0-1.0)
MIN_FAIL_HOSTS="10"              # Мін. недоступних хостів

# Телефони сервісних служб
SECURITY_PHONE="+380XXXXXXXXX"
PLUMBER_PHONE="+380XXXXXXXXX"
ELECTRICIAN_PHONE="+380XXXXXXXXX"
ELEVATOR_PHONES="+380XXXXXXXXX"

# API ключ для тривог (https://api.ukrainealarm.com)
ALERTS_API_KEY="your_alerts_api_key_here"
```

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

# Зупинка
sudo systemctl stop bot-prod.service
```

## 🗃️ База даних

Основні таблиці:

| Таблиця | Призначення |
|---------|-------------|
| `subscribers` | Підписники бота |
| `buildings` | Будинки комплексу |
| `events` | Історія подій (світло on/off) |
| `water_votes` | Голосування за воду |
| `heating_votes` | Голосування за опалення |
| `places` | Довідник корисних місць |
| `place_likes` | Лайки місць |

## 📝 Ліцензія

MIT License

## 👨‍💻 Автор

Створено для жителів ЖК з ❤️
