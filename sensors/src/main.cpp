/*
 * PowerBot ESP32-S3-POE-ETH Heartbeat Sensor
 * 
 * Плата: Waveshare ESP32-S3-POE-ETH-CAM-KIT
 * 
 * Відправляє heartbeat на сервер кожні 60 секунд.
 * Коли сенсор онлайн - світло в будинку є.
 * Коли сенсор офлайн (немає heartbeat > 150 сек) - світла немає.
 */

#include <Arduino.h>
#include <SPI.h>
#include <ETH.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

// Стан Ethernet
static bool eth_connected = false;

// Час останнього heartbeat
unsigned long lastHeartbeatTime = 0;

// Прототипи функцій
void setupEthernet();
void onEthEvent(arduino_event_id_t event);
bool sendHeartbeat();
void blinkLED(int times, int delayMs);

void setup() {
    Serial.begin(115200);
    delay(1000);
    
    Serial.println();
    Serial.println("================================================");
    Serial.println("  PowerBot ESP32-S3-POE-ETH Heartbeat Sensor");
    Serial.println("  Плата: Waveshare ESP32-S3-POE-ETH-CAM-KIT");
    Serial.printf("  Building: %s\n", BUILDING_NAME);
    Serial.printf("  Sensor:   %s\n", SENSOR_UUID);
    Serial.printf("  Server:   %s:%d\n", SERVER_IP, SERVER_PORT);
    Serial.println("================================================");
    Serial.println();
    
    // Налаштування LED
    #ifdef LED_PIN
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    #endif
    
    // Ініціалізація Ethernet
    setupEthernet();
}

void loop() {
    // Чекаємо підключення до мережі
    if (!eth_connected) {
        Serial.println("⏳ Очікування Ethernet з'єднання...");
        blinkLED(1, 500);  // Повільне блимання - немає мережі
        delay(1000);
        return;
    }
    
    // Перевіряємо чи час відправляти heartbeat
    unsigned long currentTime = millis();
    
    if (lastHeartbeatTime == 0 || (currentTime - lastHeartbeatTime) >= HEARTBEAT_INTERVAL_MS) {
        Serial.println();
        Serial.println("📤 Відправка heartbeat...");
        
        if (sendHeartbeat()) {
            Serial.println("✅ Heartbeat відправлено успішно!");
            blinkLED(1, 100);  // Короткий блимк - успіх
        } else {
            Serial.println("❌ Помилка відправки heartbeat!");
            blinkLED(3, 200);  // 3 блимки - помилка
        }
        
        lastHeartbeatTime = currentTime;
        
        // Показуємо час до наступного heartbeat
        Serial.printf("⏰ Наступний heartbeat через %d секунд\n", HEARTBEAT_INTERVAL_MS / 1000);
    }
    
    delay(100);
}

/**
 * Налаштування Ethernet для Waveshare ESP32-S3-POE-ETH
 */
void setupEthernet() {
    Serial.println("🔌 Ініціалізація Ethernet (W5500)...");
    
    // Реєструємо обробник подій
    WiFi.onEvent(onEthEvent);
    
    // Налаштування SPI для W5500
    SPI.begin(ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI, ETH_PHY_CS);
    
    // Ініціалізація W5500 Ethernet
    // Параметри: type, addr, cs, irq, rst, spi
    ETH.begin(ETH_PHY_W5500, ETH_PHY_ADDR, ETH_PHY_CS, ETH_PHY_IRQ, ETH_PHY_RST, SPI);
    
    Serial.println("🔌 Ethernet ініціалізовано, очікування DHCP...");
}

/**
 * Обробник подій Ethernet
 */
void onEthEvent(arduino_event_id_t event) {
    switch (event) {
        case ARDUINO_EVENT_ETH_START:
            Serial.println("🔌 ETH: Старт");
            ETH.setHostname(SENSOR_UUID);
            break;
            
        case ARDUINO_EVENT_ETH_CONNECTED:
            Serial.println("🔗 ETH: Підключено до мережі");
            break;
            
        case ARDUINO_EVENT_ETH_GOT_IP:
            Serial.println("════════════════════════════════════");
            Serial.print("🌐 IP адреса:  ");
            Serial.println(ETH.localIP());
            Serial.print("📡 MAC адреса: ");
            Serial.println(ETH.macAddress());
            Serial.print("🚀 Швидкість:  ");
            Serial.print(ETH.linkSpeed());
            Serial.println(" Mbps");
            Serial.print("📶 Full Duplex: ");
            Serial.println(ETH.fullDuplex() ? "Так" : "Ні");
            Serial.println("════════════════════════════════════");
            eth_connected = true;
            break;
            
        case ARDUINO_EVENT_ETH_DISCONNECTED:
            Serial.println("❌ ETH: Відключено від мережі!");
            eth_connected = false;
            break;
            
        case ARDUINO_EVENT_ETH_STOP:
            Serial.println("🛑 ETH: Зупинено");
            eth_connected = false;
            break;
            
        default:
            break;
    }
}

/**
 * Відправка heartbeat на сервер
 */
bool sendHeartbeat() {
    HTTPClient http;
    
    Serial.printf("🌐 URL: %s\n", API_ENDPOINT);
    
    // Починаємо з'єднання
    http.begin(API_ENDPOINT);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(HTTP_TIMEOUT_MS);
    
    // Формуємо JSON
    JsonDocument doc;
    doc["api_key"] = API_KEY;
    doc["building_id"] = BUILDING_ID;
    doc["sensor_uuid"] = SENSOR_UUID;
    
    String payload;
    serializeJson(doc, payload);
    
    Serial.printf("📦 Payload: %s\n", payload.c_str());
    
    // Відправляємо POST запит
    int httpCode = http.POST(payload);
    
    Serial.printf("📡 HTTP код: %d\n", httpCode);
    
    bool success = false;
    
    if (httpCode > 0) {
        String response = http.getString();
        Serial.printf("📨 Відповідь: %s\n", response.c_str());
        
        if (httpCode == HTTP_CODE_OK) {
            success = true;
        } else {
            Serial.printf("⚠️ Сервер повернув код %d\n", httpCode);
        }
    } else {
        Serial.printf("❌ HTTP помилка: %s\n", http.errorToString(httpCode).c_str());
    }
    
    http.end();
    return success;
}

/**
 * Блимання LED
 */
void blinkLED(int times, int delayMs) {
    #ifdef LED_PIN
    for (int i = 0; i < times; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(delayMs);
        digitalWrite(LED_PIN, LOW);
        if (i < times - 1) delay(delayMs);
    }
    #endif
}
