/*
 * PowerBot ESP32 Ethernet Heartbeat Sensor
 *
 * Плати: WT32-ETH01 / ESP32-ETH01 (LAN8720, RMII)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ETH.h>
#include <ArduinoJson.h>
#include "config.h"

// Ethernet/TCP клієнт
WiFiClient ethClient;

// Для коректного логування в різних env (див. platformio.ini)
#ifndef PB_BOARD_NAME
#define PB_BOARD_NAME "ESP32 Ethernet"
#endif

// Стан підключення
bool eth_connected = false;

// Час останнього heartbeat
unsigned long lastHeartbeatTime = 0;

// Прототипи функцій
void onEthEvent(WiFiEvent_t event);
void setupEthernet();
bool sendHeartbeat();
void blinkLED(int times, int delayMs);

void setup() {
    Serial.begin(115200);
    delay(2000);

    Serial.println();
    Serial.println("================================================");
    Serial.println("  PowerBot ESP32 Ethernet Heartbeat Sensor");
    Serial.print("  Board:    ");
    Serial.println(PB_BOARD_NAME);
    Serial.printf("  Building: %s (ID: %d)\n", BUILDING_NAME, BUILDING_ID);
    Serial.printf("  Sensor:   %s\n", SENSOR_UUID);
    Serial.printf("  Server:   %s:%d\n", SERVER_HOST, SERVER_PORT);
    Serial.println("================================================");
    Serial.println();

#if defined(LED_PIN) && (LED_PIN >= 0)
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
#endif

    WiFi.onEvent(onEthEvent);
    setupEthernet();
}

void loop() {
    if (!eth_connected || !ETH.linkUp()) {
        if (eth_connected && !ETH.linkUp()) {
            Serial.println("❌ Ethernet link down!");
            eth_connected = false;
        }
        blinkLED(1, 500);
        delay(1000);
        return;
    }

    // Перевіряємо чи час відправляти heartbeat
    const unsigned long currentTime = millis();
    if (lastHeartbeatTime == 0 || (currentTime - lastHeartbeatTime) >= HEARTBEAT_INTERVAL_MS) {
        Serial.println();
        Serial.println("📤 Відправка heartbeat...");

        if (sendHeartbeat()) {
            Serial.println("✅ Heartbeat успішно!");
            blinkLED(1, 100);
        } else {
            Serial.println("❌ Помилка heartbeat!");
            blinkLED(3, 200);
        }

        lastHeartbeatTime = currentTime;
        Serial.printf("⏰ Наступний через %d сек\n", HEARTBEAT_INTERVAL_MS / 1000);
    }

    delay(100);
}

void onEthEvent(WiFiEvent_t event) {
    switch (event) {
        case ARDUINO_EVENT_ETH_START:
            ETH.setHostname(SENSOR_UUID);
            Serial.println("🔌 ETH start");
            break;

        case ARDUINO_EVENT_ETH_CONNECTED:
            Serial.println("🔗 ETH link up");
            break;

        case ARDUINO_EVENT_ETH_GOT_IP:
            Serial.println("✅ ETH got IP");
            Serial.print("🌐 IP адреса:  ");
            Serial.println(ETH.localIP());
            Serial.print("🌐 Gateway:    ");
            Serial.println(ETH.gatewayIP());
            Serial.print("🌐 DNS:        ");
            Serial.println(ETH.dnsIP());
            Serial.print("🌐 Subnet:     ");
            Serial.println(ETH.subnetMask());
            Serial.print("📡 MAC:        ");
            Serial.println(ETH.macAddress());
            eth_connected = true;
            break;

        case ARDUINO_EVENT_ETH_DISCONNECTED:
            Serial.println("❌ ETH disconnected");
            eth_connected = false;
            break;

        case ARDUINO_EVENT_ETH_STOP:
            Serial.println("🛑 ETH stopped");
            eth_connected = false;
            break;

        default:
            break;
    }
}

void setupEthernet() {
    Serial.println("🔌 Ініціалізація LAN8720...");
    Serial.printf("   PHY_ADDR=%d, POWER=%d\n", PB_ETH_PHY_ADDR, PB_ETH_PHY_POWER);
    Serial.printf("   MDC=%d, MDIO=%d\n", PB_ETH_PHY_MDC, PB_ETH_PHY_MDIO);
    Serial.printf("   CLK_MODE=%d (0=GPIO0_IN,1=GPIO0_OUT,2=GPIO16_OUT,3=GPIO17_OUT)\n", static_cast<int>(PB_ETH_CLK_MODE));

    if (!ETH.begin(PB_ETH_PHY_ADDR,
                   PB_ETH_PHY_POWER,
                   PB_ETH_PHY_MDC,
                   PB_ETH_PHY_MDIO,
                   PB_ETH_PHY_TYPE,
                   PB_ETH_CLK_MODE)) {
        Serial.println("❌ Помилка запуску Ethernet!");
        return;
    }

    Serial.println("📡 Очікування DHCP...");
    const unsigned long waitStart = millis();
    while (!eth_connected && (millis() - waitStart) < 15000) {
        delay(100);
    }

    if (!eth_connected) {
        Serial.println("❌ DHCP не вдалося отримати за 15 секунд");
        return;
    }

    Serial.println("════════════════════════════════════");
    Serial.println("✅ Ethernet готовий");
    Serial.println("════════════════════════════════════");
}

bool sendHeartbeat() {
    Serial.printf("🌐 Підключення до %s:%d...\n", SERVER_HOST, SERVER_PORT);
    Serial.printf("   Local IP: %s\n", ETH.localIP().toString().c_str());
    Serial.printf("   Gateway:  %s\n", ETH.gatewayIP().toString().c_str());
    Serial.printf("   Link:     %s\n", ETH.linkUp() ? "ON" : "OFF");

    ethClient.setTimeout(HTTP_TIMEOUT_MS);

    Serial.println("   Спроба connect()...");
    const bool connected = ethClient.connect(SERVER_HOST, SERVER_PORT);
    Serial.printf("   Connect result: %d\n", connected ? 1 : 0);

    if (!connected) {
        Serial.println("❌ Не вдалося підключитися до сервера!");
        Serial.println("   Можливі причини:");
        Serial.println("   - Немає маршруту до інтернету");
        Serial.println("   - Firewall блокує з'єднання");
        Serial.println("   - Сервер недоступний");
        return false;
    }

    // Формуємо JSON
    JsonDocument doc;
    doc["api_key"] = API_KEY;
    doc["building_id"] = BUILDING_ID;
    doc["sensor_uuid"] = SENSOR_UUID;

    String payload;
    serializeJson(doc, payload);

    Serial.printf("📦 Payload: %s\n", payload.c_str());

    // HTTP POST запит
    ethClient.println("POST /api/v1/heartbeat HTTP/1.1");
    ethClient.print("Host: ");
    ethClient.println(SERVER_HOST);
    ethClient.println("Content-Type: application/json");
    ethClient.println("Connection: close");
    ethClient.print("Content-Length: ");
    ethClient.println(payload.length());
    ethClient.println();
    ethClient.println(payload);

    // Чекаємо відповідь
    const unsigned long timeout = millis();
    while (!ethClient.available()) {
        if (millis() - timeout > HTTP_TIMEOUT_MS) {
            Serial.println("❌ Таймаут відповіді!");
            ethClient.stop();
            return false;
        }
        delay(10);
    }

    // Читаємо статус
    const String statusLine = ethClient.readStringUntil('\n');
    Serial.printf("📨 %s\n", statusLine.c_str());

    const bool success = statusLine.indexOf(" 200 ") > 0;

    // Пропускаємо заголовки
    while (ethClient.available()) {
        const String line = ethClient.readStringUntil('\n');
        if (line == "\r") {
            break;
        }
    }

    // Читаємо body
    String body = "";
    while (ethClient.available()) {
        body += static_cast<char>(ethClient.read());
    }
    if (body.length() > 0) {
        Serial.printf("📨 Body: %s\n", body.c_str());
    }

    ethClient.stop();
    return success;
}

void blinkLED(int times, int delayMs) {
#if defined(LED_PIN) && (LED_PIN >= 0)
    for (int i = 0; i < times; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(delayMs);
        digitalWrite(LED_PIN, LOW);
        if (i < times - 1) {
            delay(delayMs);
        }
    }
#endif
}
