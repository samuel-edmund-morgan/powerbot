/*
 * PowerBot ESP32-S3-POE-ETH Heartbeat Sensor
 * 
 * Плата: Waveshare ESP32-S3-POE-ETH-CAM-KIT
 * Ethernet: W5500 через SPI
 */

#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <ArduinoJson.h>
#include "config.h"

// MAC адреса (унікальна для кожного пристрою)
byte mac[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, BUILDING_ID };

// Ethernet клієнт
EthernetClient ethClient;

// Стан підключення
bool eth_connected = false;

// Час останнього heartbeat
unsigned long lastHeartbeatTime = 0;

// Прототипи функцій
void setupEthernet();
bool sendHeartbeat();
void blinkLED(int times, int delayMs);

void setup() {
    Serial.begin(115200);
    delay(2000);
    
    Serial.println();
    Serial.println("================================================");
    Serial.println("  PowerBot ESP32-S3-POE-ETH Heartbeat Sensor");
    Serial.println("  Плата: Waveshare ESP32-S3-POE-ETH-CAM-KIT");
    Serial.printf("  Building: %s (ID: %d)\n", BUILDING_NAME, BUILDING_ID);
    Serial.printf("  Section:  %d\n", SECTION_ID);
    Serial.printf("  Sensor:   %s\n", SENSOR_UUID);
    Serial.printf("  Server:   %s:%d\n", SERVER_HOST, SERVER_PORT);
    Serial.println("================================================");
    Serial.println();
    
    #ifdef LED_PIN
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    #endif
    
    setupEthernet();
}

void loop() {
    // Підтримуємо DHCP lease
    Ethernet.maintain();
    
    // Перевіряємо стан Ethernet
    auto link = Ethernet.linkStatus();
    
    if (link == LinkOFF) {
        if (eth_connected) {
            Serial.println("❌ Ethernet кабель відключено!");
            eth_connected = false;
        }
        blinkLED(1, 500);
        delay(1000);
        return;
    }
    
    if (!eth_connected && Ethernet.localIP() != IPAddress(0,0,0,0) && 
        Ethernet.localIP() != IPAddress(255,255,255,255)) {
        Serial.println("🔗 Ethernet підключено!");
        Serial.print("🌐 IP: ");
        Serial.println(Ethernet.localIP());
        eth_connected = true;
    }
    
    if (!eth_connected) {
        delay(1000);
        return;
    }
    
    // Перевіряємо чи час відправляти heartbeat
    unsigned long currentTime = millis();
    
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

void setupEthernet() {
    Serial.println("🔌 Ініціалізація W5500...");
    Serial.printf("   SPI: SCK=%d, MISO=%d, MOSI=%d\n", 
                  ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI);
    Serial.printf("   CS=%d, RST=%d\n", ETH_PHY_CS, ETH_PHY_RST);
    
    // 1. Апаратне скидання W5500 через RST pin
    Serial.println("   Скидання W5500...");
    pinMode(ETH_PHY_RST, OUTPUT);
    digitalWrite(ETH_PHY_RST, LOW);
    delay(100);
    digitalWrite(ETH_PHY_RST, HIGH);
    delay(500);
    Serial.println("   ✓ W5500 скинуто");
    
    // 2. Налаштування CS pin
    pinMode(ETH_PHY_CS, OUTPUT);
    digitalWrite(ETH_PHY_CS, HIGH);
    
    // 3. Ініціалізація SPI з пінами Waveshare
    // ВАЖЛИВО: передаємо піни в правильному порядку для ESP32
    SPI.begin(ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI, ETH_PHY_CS);
    Serial.println("   ✓ SPI ініціалізовано");
    
    // 4. Вказуємо CS pin для Ethernet бібліотеки
    Ethernet.init(ETH_PHY_CS);
    
    delay(100);
    
    Serial.println("📡 Отримання IP через DHCP...");
    
    // Спроба отримати IP через DHCP
    if (Ethernet.begin(mac, 15000, 4000)) {
        Serial.println("════════════════════════════════════");
        Serial.print("🌐 IP адреса:  ");
        Serial.println(Ethernet.localIP());
        Serial.print("🌐 Gateway:    ");
        Serial.println(Ethernet.gatewayIP());
        Serial.print("🌐 DNS:        ");
        Serial.println(Ethernet.dnsServerIP());
        Serial.print("🌐 Subnet:     ");
        Serial.println(Ethernet.subnetMask());
        Serial.print("📡 MAC:        ");
        for (int i = 0; i < 6; i++) {
            if (mac[i] < 16) Serial.print("0");
            Serial.print(mac[i], HEX);
            if (i < 5) Serial.print(":");
        }
        Serial.println();
        Serial.println("════════════════════════════════════");
        eth_connected = true;
    } else {
        Serial.println("❌ DHCP не вдалося!");
        
        auto hw = Ethernet.hardwareStatus();
        Serial.printf("   Hardware status: %d ", hw);
        
        if (hw == EthernetNoHardware) {
            Serial.println("(No Hardware)");
            Serial.println("❌ W5500 не знайдено!");
            Serial.println("   Перевірте SPI підключення");
        } else if (hw == EthernetW5100) {
            Serial.println("(W5100)");
        } else if (hw == EthernetW5200) {
            Serial.println("(W5200)");
        } else if (hw == EthernetW5500) {
            Serial.println("(W5500)");
            Serial.println("✅ W5500 знайдено!");
            if (Ethernet.linkStatus() == LinkOFF) {
                Serial.println("❌ Ethernet кабель не підключено!");
            } else {
                Serial.println("⚠️ DHCP сервер не відповідає");
            }
        } else {
            Serial.println("(Unknown)");
        }
    }
}

bool sendHeartbeat() {
    Serial.printf("🌐 Підключення до %s:%d...\n", SERVER_HOST, SERVER_PORT);
    
    // Перевіряємо стан мережі
    Serial.printf("   Local IP: %s\n", Ethernet.localIP().toString().c_str());
    Serial.printf("   Gateway:  %s\n", Ethernet.gatewayIP().toString().c_str());
    Serial.printf("   Link:     %s\n", Ethernet.linkStatus() == LinkON ? "ON" : "OFF");
    
    // Перетворюємо IP рядок в IPAddress
    // Таймаут підключення
    ethClient.setTimeout(10000);
    
    Serial.println("   Спроба connect()...");
    IPAddress serverIP;
    int result = 0;
    if (serverIP.fromString(SERVER_HOST)) {
        Serial.printf("   Parsed IP: %s\n", serverIP.toString().c_str());
        result = ethClient.connect(serverIP, SERVER_PORT);
    } else {
        result = ethClient.connect(SERVER_HOST, SERVER_PORT);
    }
    Serial.printf("   Connect result: %d\n", result);
    
    if (!result) {
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
    doc["section_id"] = SECTION_ID;
    doc["sensor_uuid"] = SENSOR_UUID;
#if defined(SENSOR_COMMENT)
    if (String(SENSOR_COMMENT).length() > 0) {
        doc["comment"] = SENSOR_COMMENT;
    }
#endif
    
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
    unsigned long timeout = millis();
    while (ethClient.available() == 0) {
        if (millis() - timeout > HTTP_TIMEOUT_MS) {
            Serial.println("❌ Таймаут відповіді!");
            ethClient.stop();
            return false;
        }
    }
    
    // Читаємо статус
    String statusLine = ethClient.readStringUntil('\n');
    Serial.printf("📨 %s\n", statusLine.c_str());
    
    bool success = statusLine.indexOf("200") > 0;
    
    // Пропускаємо заголовки
    while (ethClient.available()) {
        String line = ethClient.readStringUntil('\n');
        if (line == "\r") break;
    }
    
    // Читаємо body
    String body = "";
    while (ethClient.available()) {
        body += (char)ethClient.read();
    }
    if (body.length() > 0) {
        Serial.printf("📨 Body: %s\n", body.c_str());
    }
    
    ethClient.stop();
    return success;
}

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
