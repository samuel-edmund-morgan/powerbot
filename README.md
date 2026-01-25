# PowerBot

Telegram-бот для ЖК "Нова Англія" з підтримкою ESP32 сенсорів та HTTP API.

Проєкт підтримує **тільки Docker-деплой**. Ручна інсталяція без контейнера більше не підтримується.

## Швидкий старт (Docker)

1) Підготуйте робочу директорію (наприклад `/opt/powerbot`):
```bash
mkdir -p /opt/powerbot
cd /opt/powerbot
```

2) Скопіюйте файли з репозиторію:
- `docker-compose.yml`
- `.env.example` → `.env`

3) Створіть порожню базу:
```bash
touch state.db
```

4) Заповніть `.env` (мінімум: `BOT_TOKEN`, `SENSOR_API_KEY`).

5) Запустіть контейнер:
```bash
docker compose up -d
```

Після першого запуску контейнер сам ініціалізує `state.db` зі схеми, що вшита в образ.

## Встановлення Docker (Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```
Після додавання в групу `docker` перезайдіть у сесію.

## Структура проєкту

```
.
├── src/                  # Код бота
├── docker/               # entrypoint та інше для контейнера
├── sensors/              # Прошивка ESP32
├── scripts/              # Допоміжні утиліти
├── schema.sql            # Схема БД + початкові дані
├── Dockerfile            # Образ бота
├── Dockerfile.migrate    # Образ для міграцій БД
├── docker-compose.yml    # Docker Compose
└── .env.example          # Шаблон змінних середовища
```

## Команди керування

```bash
# старт / оновлення
docker compose up -d

# зупинка
docker compose down

# перезапуск
docker compose restart

# логи
docker compose logs -f powerbot

# статус
docker compose ps
```

## Оновлення образу

```bash
docker compose pull
docker compose up -d
```

## Міграції БД

Міграції додають **нові таблиці/колонки** і не видаляють дані.

1) Зупиніть бот:
```bash
docker compose down
```

2) Запустіть міграцію:
```bash
docker compose --profile migrate run --rm migrate
```

3) Запустіть бот знову:
```bash
docker compose up -d
```

## Бекап / відновлення

Найнадійніше — зробити гарячий бекап через sqlite3:
```bash
sqlite3 state.db ".backup 'state.db.$(date +%F_%H-%M-%S).bak'"
```

Або зупинити контейнер і просто скопіювати файл:
```bash
docker compose down
cp state.db state.db.bak
docker compose up -d
```

## Два боти на одному хості

Рекомендований підхід — **дві окремі директорії** з власними `.env` і `state.db`:

```
/opt/powerbot
/opt/powerbot-test
```

У кожній папці свій `docker-compose.yml` з різним портом:
- prod: `"18081:8081"`
- test: `"18082:8081"`

## API для сенсорів

Endpoint:
```
POST http://<host>:18081/api/v1/heartbeat
```

Payload:
```json
{
  "api_key": "<SENSOR_API_KEY>",
  "building_id": 1,
  "sensor_uuid": "esp32-newcastle-001"
}
```

## Розробка

Розробка ведеться в `src/`. Для локальних змін достатньо оновити код і зібрати Docker-образ.
