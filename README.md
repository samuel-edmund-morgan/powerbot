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

# логи (з фільтром)
docker compose logs -f powerbot | rg "INFO:handlers:User"

# статус
docker compose ps

# разова міграція (якщо є зміни схеми)
docker compose --profile migrate run --rm migrate
```

Бекап (краще гарячий):
```bash
sqlite3 state.db ".backup 'state.db.$(date +%F_%H-%M-%S).bak'"
```

## 3) Відновлення з критичних ситуацій

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
  -d '{"api_key":"<SENSOR_API_KEY>","building_id":1,"sensor_uuid":"esp32-newcastle-001"}'
```
