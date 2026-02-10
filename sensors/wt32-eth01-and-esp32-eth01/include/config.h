/*
 * Конфігурація ESP32 Ethernet (WT32-ETH01 / ESP32-ETH01) для PowerBot
 *
 * Ethernet: LAN8720 через RMII (вбудований MAC ESP32)
 */

#ifndef CONFIG_H
#define CONFIG_H

// ═══════════════════════════════════════════════════════════════
// НАЛАШТУВАННЯ СЕРВЕРА
// ═══════════════════════════════════════════════════════════════

// Домен або IP сервера (HTTP)
#define SERVER_HOST     "sensors-new-england.morgan-dev.com"

// Порт HTTP API (prod = 18081, test = 18082 якщо два контейнери)
#define SERVER_PORT     18081

// API ключ (однаковий для всіх сенсорів)
#define API_KEY         "e083c38d50d164ea1f9d4491147b73df1b42741675daa8e3f520800eccebd08c"

// ═══════════════════════════════════════════════════════════════
// НАЛАШТУВАННЯ СЕНСОРА
// ═══════════════════════════════════════════════════════════════

// ID будинку (1-14, див. список нижче)
#define BUILDING_ID     1

// Номер секції (1..3) в межах будинку
#define SECTION_ID      2

// Опціональна примітка (наприклад: "кв 123"). Залиш порожнім якщо не потрібно.
#define SENSOR_COMMENT  ""

// Унікальний ідентифікатор сенсора
#define SENSOR_UUID     "esp32-newcastle-002"

// Назва будинку (для логів)
#define BUILDING_NAME   "Newcastle"

// ═══════════════════════════════════════════════════════════════
// ТАЙМІНГИ
// ═══════════════════════════════════════════════════════════════

// Інтервал відправки heartbeat (10 секунд)
#define HEARTBEAT_INTERVAL_MS   10000

// Таймаут HTTP запиту (10 секунд)
#define HTTP_TIMEOUT_MS         10000

// ═══════════════════════════════════════════════════════════════
// Ethernet PHY (LAN8720, RMII)
//
// За замовчуванням виставлено під WT32-ETH01 (external 50MHz clock -> GPIO0).
// Для багатьох ESP32-ETH01 клонів потрібен clock OUT від ESP32 (GPIO0_OUT або GPIO17_OUT).
// Це задається через build_flags у `platformio.ini` (див. env:esp32-eth01).
//
// Якщо `PB_ETH_AUTOCONFIG=1` — firmware автоматично перебирає кілька типових
// профілів (addr/clock/reset/pwr_en) на різних перезавантаженнях і зберігає
// робочий профіль в NVS. Це зроблено, бо ESP32-ETH01/WT32-ETH01 "клони" часто
// відрізняються саме цими параметрами.
//
// ВАЖЛИВО: в Arduino-ESP32 `ETH.begin(..., power, ...)` у сучасних версіях
// передає `power` як `reset_gpio_num` у ESP-IDF (тобто це скоріше RESET pin, а не PWR_EN).
// На деяких платах є окремий PWR_EN для PHY (його потрібно просто виставити в HIGH перед ETH.begin()).
// ═══════════════════════════════════════════════════════════════

#ifndef PB_ETH_AUTOCONFIG
#define PB_ETH_AUTOCONFIG  0
#endif

// Якщо PB_ETH_AUTOCONFIG=1, можна підказати який PHY очікуємо.
// Це не вимикає інші варіанти, але дозволяє стартувати зі "схожих" профілів.
// Приклад для ESP32-ETH01 з IC+ IP101: -DPB_ETH_AUTOCONFIG_PREFERRED_PHY=ETH_PHY_IP101
#ifndef PB_ETH_AUTOCONFIG_PREFERRED_PHY
#define PB_ETH_AUTOCONFIG_PREFERRED_PHY  ETH_PHY_MAX
#endif

#ifndef PB_ETH_PHY_ADDR
#define PB_ETH_PHY_ADDR    1
#endif

#ifndef PB_ETH_PHY_POWER
#define PB_ETH_PHY_POWER   16
#endif

#ifndef PB_ETH_PHY_MDC
#define PB_ETH_PHY_MDC     23
#endif

#ifndef PB_ETH_PHY_MDIO
#define PB_ETH_PHY_MDIO    18
#endif

#ifndef PB_ETH_PHY_TYPE
#define PB_ETH_PHY_TYPE    ETH_PHY_LAN8720
#endif

#ifndef PB_ETH_CLK_MODE
#define PB_ETH_CLK_MODE    ETH_CLOCK_GPIO0_IN
#endif

// Опційний power enable pin для PHY (якщо на платі він є).
// За замовчуванням вимкнено (-1).
#ifndef PB_ETH_POWER_ENABLE_PIN
#define PB_ETH_POWER_ENABLE_PIN  -1
#endif

// Рівень, який вмикає живлення PHY на PB_ETH_POWER_ENABLE_PIN (1 = HIGH, 0 = LOW)
#ifndef PB_ETH_POWER_ENABLE_LEVEL
#define PB_ETH_POWER_ENABLE_LEVEL  1
#endif

// Затримка після увімкнення PHY power enable (мс)
#ifndef PB_ETH_POWER_UP_DELAY_MS
#define PB_ETH_POWER_UP_DELAY_MS  150
#endif

// ═══════════════════════════════════════════════════════════════
// LED ІНДИКАЦІЯ
// ═══════════════════════════════════════════════════════════════

// На більшості ревізій WT32-ETH01 немає user LED.
// Якщо у твоїй ревізії є індикатор - розкоментуй і вкажи pin.
// #define LED_PIN      2

// ═══════════════════════════════════════════════════════════════
// СПИСОК БУДИНКІВ ЖК "НОВА АНГЛІЯ"
// ═══════════════════════════════════════════════════════════════
/*
    ID  | Назва       | Адреса  | UUID сенсора
    ----|-------------|---------|----------------------
    1   | Ньюкасл     | 24-в    | esp32-newcastle-001
    2   | Брістоль    | 24-б    | esp32-bristol-001
    3   | Ліверпуль   | 24-а    | esp32-liverpool-001
    4   | Ноттінгем   | 24-г    | esp32-nottingham-001
    5   | Манчестер   | 26-г    | esp32-manchester-001
    6   | Кембрідж    | 26      | esp32-cambridge-001
    7   | Брайтон     | 26-в    | esp32-brighton-001
    8   | Бермінгем   | 26-б    | esp32-birmingham-001
    9   | Віндзор     | 26-д    | esp32-windsor-001
    10  | Честер      | 28-д    | esp32-chester-001
    11  | Лондон      | 28-е    | esp32-london-001
    12  | Оксфорд     | 28-б    | esp32-oxford-001
    13  | Лінкольн    | 28-к    | esp32-lincoln-001
    14  | Престон     | Престон | esp32-preston-001
*/

#endif // CONFIG_H
