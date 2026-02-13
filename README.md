# PowerBot

Це особиста документація для відновлення та керування ботом. Проєкт працює **тільки через Docker**.

## 1) Початкова інсталяція (з нуля)

### 1.1 Встановити Docker (Ubuntu)
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
Після додавання в групу `docker` — перелогінься.

### 1.2 Підготувати директорію та файли
```bash
mkdir -p /opt/powerbot
cd /opt/powerbot
```
Для першої інсталяції ці файли беруться так:
- `docker-compose.yml` — з репозиторію (GitHub) або з локальної копії проєкту.
- `.env` — створити з `.env.example` (з репозиторію) і заповнити свої значення.
- `state.db` — створити порожній файл (бот ініціалізує структуру сам).

```bash
touch state.db
```

Важливо:
- у `.env` має бути `DB_PATH="/data/state.db"`
- `API_PORT=8081`

### 1.3 Запуск
```bash
docker compose pull
docker compose up -d
```
Перевірити:
```bash
docker compose ps
curl -v http://127.0.0.1:18081/api/v1/health
```

## 2) Команди для керування контейнерами

```bash
# старт / оновлення
docker compose up -d

# зупинка
docker compose down

# перезапуск
docker compose restart

# оновити image
docker compose pull
docker compose up -d

# логи контейнера (з фільтром)
docker compose logs -f powerbot | grep -E "INFO:handlers:User|INFO:aiohttp.access"

# persistent лог-файл (не зникає після recreate контейнера)
tail -f /opt/powerbot/logs/powerbot.log

# test persistent лог-файли
tail -f /opt/powerbot-test/logs/powerbot.log
tail -f /opt/powerbot-test/logs/businessbot.log

# з локального Mac через SSH
ssh workspace-docker "tail -f /opt/powerbot/logs/powerbot.log"
ssh workspace-docker "tail -f /opt/powerbot-test/logs/powerbot.log"
ssh workspace-docker "tail -f /opt/powerbot-test/logs/businessbot.log"

# статус
docker compose ps

# разова міграція (якщо є зміни схеми)
docker compose --profile migrate run --rm migrate
```

Бекап (краще гарячий):
```bash
sqlite3 state.db ".backup 'state.db.$(date +%F_%H-%M-%S).bak'"
```

Автобекап через `/usr/local/bin/powerbot-backup.sh` архівує:
- `/opt/powerbot` (включно з `/opt/powerbot/logs`)
- `/opt/powerbot-test` (включно з `/opt/powerbot-test/logs`)
- `/opt/traefik`
- консистентні копії `state.db` через `sqlite3 .backup`

## 3) Шпаргалка (команди)

Ось базові команди керування контейнером для твого сетапу (припускаю, що docker-compose.yml, .env і state.db лежать в одному каталозі).

```bash
# старт (або оновлення) у фоні
docker compose up -d

# зупинка контейнерів
docker compose down

# перезапуск сервісу
docker compose restart

# Оновлення на новий image
docker compose pull
docker compose up -d

# Логи
docker compose logs -f powerbot

# tail + grep
docker compose logs -f powerbot | rg "INFO:handlers:User"

# Статус
docker compose ps

# Разова міграція (якщо треба)
docker compose --profile migrate run --rm migrate
```

Бекап / відновлення state.db:
- з Google Drive беру останній бекап і виконую:
```bash
docker compose restart
```

Деплой:
```bash
# Тест-деплой
git add . && git commit -m "<опис змін>" && git push origin main

# Прод-деплой
gh workflow run deploy.yml -f run_migrate=auto
```

## 4) Відновлення з критичних ситуацій

Якщо сервер видалили/впав:
1) Створи або орендуй новий сервер, дізнайся його зовнішній IP.
2) У Cloudflare зміни DNS A запис `new-england.morgan-dev.com` на нову IP адресу.
3) Якщо на хостингу є фаєрвол — відкрий порт **18081**.
4) Залогінься на сервер і відкрий порт у `ufw`:
```bash
sudo ufw allow 18081/tcp
```
5) Встанови Docker (див. команди з розділу 1.1).
6) Перекинь у `/opt/powerbot` останні бекапи файлів:
- `.env`
- `state.db`
- `docker-compose.yml`

Примітка: якщо в тебе увімкнений rclone‑бекап, щоденні архіви лежать у Google Drive
в папці `powerbot/` (файли виду `powerbot-backup-YYYY-MM-DD_HH-MM-SS.tar.gz`).

7) Запусти контейнер:
```bash
cd /opt/powerbot
docker compose pull
docker compose up -d
```
8) Перевір:
```bash
docker compose ps
curl -v http://127.0.0.1:18081/api/v1/health
```
9) Для “пробудження” стану сенсора зроби перший heartbeat:
```bash
curl -X POST http://new-england.morgan-dev.com:18081/api/v1/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"api_key":"<SENSOR_API_KEY>","building_id":1,"section_id":2,"sensor_uuid":"esp32-newcastle-001","comment":"кв. 123"}'
```

## 5) Business Mode: ізоляція та перемикання

Бізнес-функціонал ізольований feature-flag’ом і за замовчуванням не впливає на мешканців.

Ключові змінні:
- `BUSINESS_MODE=0|1`
- `BUSINESS_BOT_API_KEY=<token>`
- `BUSINESS_PAYMENT_PROVIDER=mock|telegram_stars`

Правила:
- `BUSINESS_MODE=0`:
  - resident-бот працює у legacy-поведінці (без бізнес-UI/verified-first ефектів);
  - бізнес-бот не має запускатися як робочий контур.
- `BUSINESS_MODE=1` + непорожній `BUSINESS_BOT_API_KEY`:
  - вмикається бізнес-контур;
  - у test рекомендовано `BUSINESS_PAYMENT_PROVIDER=mock`;
  - у prod для реальних оплат: `BUSINESS_PAYMENT_PROVIDER=telegram_stars`.

### 5.1 Перемикання в test (`/opt/powerbot-test/.env`)

1. Встановити:
```bash
BUSINESS_MODE=1
BUSINESS_BOT_API_KEY=<test token>
BUSINESS_PAYMENT_PROVIDER=mock
```
2. Запустити звичний test deploy:
```bash
git add . && git commit -m "<опис змін>" && git push origin main
```
3. Дочекатися `Deploy -> deploy_test`, перевірити simulate notifications і UAT.

Щоб вимкнути business-контур у test:
```bash
BUSINESS_MODE=0
BUSINESS_BOT_API_KEY=
```
і знову прогнати test deploy.

### 5.2 Перемикання в prod (`/opt/powerbot/.env`)

Безпечний дефолт для прода:
```bash
BUSINESS_MODE=0
BUSINESS_BOT_API_KEY=
```

Коли готово увімкнути:
```bash
BUSINESS_MODE=1
BUSINESS_BOT_API_KEY=<prod business token>
BUSINESS_PAYMENT_PROVIDER=telegram_stars
```

Після зміни `.env` виконати prod deploy через workflow:
```bash
gh workflow run deploy.yml -f run_migrate=auto
```

Перед/після прод-деплою дотримуватись чинного runbook із заморозкою сенсорів.
