# PowerBot ESP32-S3-POE-ETH Sensors

Прошивка для Waveshare ESP32-S3-POE-ETH-CAM-KIT сенсорів моніторингу електроенергії.

## Принцип роботи

ESP32 відправляє **heartbeat** на сервер кожні 60 секунд:
- ✅ Heartbeat приходить → світло в будинку є
- ❌ Heartbeat не приходить > 150 сек → світла немає (відключення)

## Швидкий старт

### 1. Встановити PlatformIO

```bash
# VSCode Extension
code --install-extension platformio.platformio-ide

# Або CLI
pip install platformio
```

### 2. Налаштувати конфігурацію

Відредагуй `include/config.h`:

```cpp
// Налаштування сервера (вже налаштовано!)
#define SERVER_HOST     "64.181.205.211"
#define SERVER_PORT     80
#define API_KEY         "e083c38d50d164ea1f9d4491147b73df1b42741675daa8e3f520800eccebd08c"

// Налаштування сенсора (зміни під свій будинок)
#define BUILDING_ID     1                      // ID будинку (1-14)
#define SENSOR_UUID     "esp32-newcastle-001"  // Унікальний ID
#define BUILDING_NAME   "Ньюкасл (24-в)"       // Для логів
```

### 3. Прошити плату

```bash
cd /home/powerbot/powerbot/sensors

# Збірка
pio run

# Прошивка
pio run --target upload

# Монітор серійного порту
pio device monitor
```

## Структура проєкту

```
sensors/
├── include/
│   └── config.h        # Конфігурація (SERVER_HOST, API_KEY, BUILDING_ID)
├── lib/                # Власні бібліотеки (порожньо)
├── src/
│   └── main.cpp        # Основний код
└── platformio.ini      # Конфігурація PlatformIO
```

## Waveshare ESP32-S3-POE-ETH-CAM-KIT

**Характеристики:**
- CPU: ESP32-S3 240MHz Dual Core
- Flash: 16MB
- PSRAM: 8MB
- Ethernet: W5500 через SPI
- Живлення: PoE (Power over Ethernet)

**Pinout для W5500:**
| Сигнал | GPIO |
|--------|------|
| CS     | 14   |
| SCK    | 12   |
| MISO   | 13   |
| MOSI   | 11   |

## Список будинків

| ID | Назва      | Адреса | UUID сенсора        |
|----|------------|--------|---------------------|
| 1  | Ньюкасл    | 24-в   | esp32-newcastle-001 |
| 2  | Брістоль   | 24-б   | esp32-bristol-001   |
| 3  | Ліверпуль  | 24-а   | esp32-liverpool-001 |
| 4  | Ноттінгем  | 24-г   | esp32-nottingham-001|
| 5  | Манчестер  | 26-г   | esp32-manchester-001|
| 6  | Кембрідж   | 26     | esp32-cambridge-001 |
| 7  | Брайтон    | 26-в   | esp32-brighton-001  |
| 8  | Бермінгем  | 26-б   | esp32-birmingham-001|
| 9  | Віндзор    | 26-д   | esp32-windsor-001   |
| 10 | Честер     | 28-д   | esp32-chester-001   |
| 11 | Лондон     | 28-е   | esp32-london-001    |
| 12 | Оксфорд    | 28-б   | esp32-oxford-001    |
| 13 | Лінкольн   | 28-к   | esp32-lincoln-001   |
| 14 | Престон    | Престон| esp32-preston-001   |

## API Endpoint

```
POST http://64.181.205.211/api/v1/heartbeat
Content-Type: application/json

{
    "api_key": "e083c38d50d164ea1f9d4491147b73df1b42741675daa8e3f520800eccebd08c",
    "building_id": 1,
    "sensor_uuid": "esp32-newcastle-001"
}
```

## LED індикація

- **Повільне блимання** (500мс) - немає мережі, очікування Ethernet
- **1 короткий блимк** (100мс) - heartbeat успішний
- **3 блимки** (200мс) - помилка відправки

## Troubleshooting

### Немає IP адреси
- Перевірте Ethernet кабель
- Перевірте що є DHCP сервер в мережі
- Подивіться Serial Monitor для діагностики

### HTTP помилка 401
- Неправильний `API_KEY` в `config.h`

### HTTP помилка 404  
- Неправильний `BUILDING_ID`

### Connection refused
- Сервер не запущений
- Неправильний `SERVER_HOST`
- Файрвол блокує порт

## Корисні команди

```bash
# Отримати інфо для конкретного будинку
python scripts/sensor_manager.py info 1

# Тестовий heartbeat (приклад для порту 18081)
python scripts/sensor_manager.py test 1 --api-base http://127.0.0.1:18081

# Список сенсорів в БД
python scripts/sensor_manager.py list
```
