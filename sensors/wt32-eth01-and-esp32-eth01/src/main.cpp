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

#if PB_ETH_AUTOCONFIG
#include <Preferences.h>
#include "esp_err.h"
#include "esp_eth_mac.h"
#include "esp_eth_com.h"
#endif

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

struct PbEthProfile {
    const char *label;
    uint8_t phy_addr;
    int reset_pin;
    int mdc_pin;
    int mdio_pin;
    eth_phy_type_t phy_type;
    eth_clock_mode_t clk_mode;
    int pwr_en_pin;
    int pwr_en_level;
    int pwr_en_delay_ms;
};

static const char *pbEthClockModeStr(eth_clock_mode_t mode) {
    switch (mode) {
        case ETH_CLOCK_GPIO0_IN:
            return "GPIO0_IN";
        case ETH_CLOCK_GPIO0_OUT:
            return "GPIO0_OUT";
        case ETH_CLOCK_GPIO16_OUT:
            return "GPIO16_OUT";
        case ETH_CLOCK_GPIO17_OUT:
            return "GPIO17_OUT";
        default:
            return "UNKNOWN";
    }
}

static const char *pbEthPhyTypeStr(eth_phy_type_t type) {
    switch (type) {
        case ETH_PHY_LAN8720:
            return "LAN8720";
        case ETH_PHY_TLK110:
            return "IP101/TLK110";
        case ETH_PHY_RTL8201:
            return "RTL8201";
        case ETH_PHY_DP83848:
            return "DP83848";
        case ETH_PHY_DM9051:
            return "DM9051";
        case ETH_PHY_KSZ8041:
            return "KSZ8041";
        case ETH_PHY_KSZ8081:
            return "KSZ8081";
        default:
            return "UNKNOWN";
    }
}

#if PB_ETH_AUTOCONFIG
static constexpr uint32_t PB_ETH_AUTOCONFIG_MAGIC = 0x50424554; // 'PBET'
static constexpr uint32_t PB_ETH_PROFILESET_VERSION = 7;
#ifndef PB_ETH_AUTOCONFIG_DETECT_WIDE
#define PB_ETH_AUTOCONFIG_DETECT_WIDE 0
#endif
RTC_NOINIT_ATTR uint32_t pb_eth_magic;
RTC_NOINIT_ATTR uint32_t pb_eth_profileset_ver;
RTC_NOINIT_ATTR uint8_t pb_eth_next_profile;
RTC_NOINIT_ATTR uint8_t pb_eth_tried_count;
RTC_NOINIT_ATTR uint8_t pb_eth_detect_done;
RTC_NOINIT_ATTR uint8_t pb_eth_detect_valid;
RTC_NOINIT_ATTR int8_t pb_eth_detect_mdc;
RTC_NOINIT_ATTR int8_t pb_eth_detect_mdio;
RTC_NOINIT_ATTR uint8_t pb_eth_detect_addr;
RTC_NOINIT_ATTR uint8_t pb_eth_profile_source; // 0=static list, 1=detected/dynamic list

struct PbEthDetectedPhy {
    eth_clock_mode_t clk_mode;
    int mdc_pin;
    int mdio_pin;
    uint8_t phy_addr;
    uint16_t id1;
    uint16_t id2;
};

static bool pbEthLooksLikeValidPhyId(uint16_t id1, uint16_t id2) {
    // Filter out common "bus floating" values.
    if (id1 == 0x0000 || id1 == 0xFFFF) {
        return false;
    }
    if (id2 == 0x0000 || id2 == 0xFFFF) {
        return false;
    }
    return true;
}

static void pbEthFillMacClockConfig(eth_mac_config_t &mac_config, eth_clock_mode_t clk_mode) {
    if (clk_mode == ETH_CLOCK_GPIO0_IN) {
        mac_config.clock_config.rmii.clock_mode = EMAC_CLK_EXT_IN;
        mac_config.clock_config.rmii.clock_gpio = EMAC_CLK_IN_GPIO;
        return;
    }

    mac_config.clock_config.rmii.clock_mode = EMAC_CLK_OUT;
    mac_config.clock_config.rmii.clock_gpio =
        (clk_mode == ETH_CLOCK_GPIO0_OUT)   ? EMAC_APPL_CLK_OUT_GPIO :
        (clk_mode == ETH_CLOCK_GPIO16_OUT)  ? EMAC_CLK_OUT_GPIO :
        (clk_mode == ETH_CLOCK_GPIO17_OUT)  ? EMAC_CLK_OUT_180_GPIO :
                                              EMAC_CLK_IN_GPIO;
}

static esp_err_t pbEthMediatorPhyRegRead(esp_eth_mediator_t *, uint32_t, uint32_t, uint32_t *) {
    return ESP_ERR_INVALID_STATE;
}

static esp_err_t pbEthMediatorPhyRegWrite(esp_eth_mediator_t *, uint32_t, uint32_t, uint32_t) {
    return ESP_ERR_INVALID_STATE;
}

static esp_err_t pbEthMediatorStackInput(esp_eth_mediator_t *, uint8_t *, uint32_t) {
    return ESP_OK;
}

static esp_err_t pbEthMediatorOnStateChanged(esp_eth_mediator_t *, esp_eth_state_t, void *) {
    return ESP_OK;
}

static bool pbEthMdioReadPhyIdRaw(eth_clock_mode_t clk_mode, int mdc, int mdio, uint8_t addr, uint16_t &out_id1, uint16_t &out_id2) {
    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    pbEthFillMacClockConfig(mac_config, clk_mode);
    mac_config.smi_mdc_gpio_num = mdc;
    mac_config.smi_mdio_gpio_num = mdio;
    mac_config.sw_reset_timeout_ms = 1000;

    esp_eth_mac_t *mac = esp_eth_mac_new_esp32(&mac_config);
    if (!mac) {
        return false;
    }

    // Important: esp32 emac implementation expects the mediator to be set before init().
    esp_eth_mediator_t mediator = {};
    mediator.phy_reg_read = pbEthMediatorPhyRegRead;
    mediator.phy_reg_write = pbEthMediatorPhyRegWrite;
    mediator.stack_input = pbEthMediatorStackInput;
    mediator.on_state_changed = pbEthMediatorOnStateChanged;
    (void)mac->set_mediator(mac, &mediator);

    bool ok = false;
    if (mac->init(mac) == ESP_OK) {
        (void)mac->start(mac);

        uint32_t id1 = 0;
        uint32_t id2 = 0;
        if (mac->read_phy_reg(mac, addr, 2, &id1) == ESP_OK &&
            mac->read_phy_reg(mac, addr, 3, &id2) == ESP_OK) {
            out_id1 = static_cast<uint16_t>(id1 & 0xFFFF);
            out_id2 = static_cast<uint16_t>(id2 & 0xFFFF);
            ok = true;
        }

        (void)mac->stop(mac);
        (void)mac->deinit(mac);
    }

    (void)mac->del(mac);
    return ok;
}

static bool pbEthMdioScanFirstHit(eth_clock_mode_t clk_mode, int mdc, int mdio, const uint8_t *addrs, size_t addr_count, PbEthDetectedPhy &out) {
    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    pbEthFillMacClockConfig(mac_config, clk_mode);
    mac_config.smi_mdc_gpio_num = mdc;
    mac_config.smi_mdio_gpio_num = mdio;
    mac_config.sw_reset_timeout_ms = 1000;

    esp_eth_mac_t *mac = esp_eth_mac_new_esp32(&mac_config);
    if (!mac) {
        return false;
    }

    bool found = false;
    // Important: esp32 emac implementation expects the mediator to be set before init()
    // (otherwise internal pointers may be null and init can crash).
    esp_eth_mediator_t mediator = {};
    mediator.phy_reg_read = pbEthMediatorPhyRegRead;
    mediator.phy_reg_write = pbEthMediatorPhyRegWrite;
    mediator.stack_input = pbEthMediatorStackInput;
    mediator.on_state_changed = pbEthMediatorOnStateChanged;
    (void)mac->set_mediator(mac, &mediator);

    if (mac->init(mac) == ESP_OK) {
        // Some implementations require MAC started for SMI access; ignore start error.
        (void)mac->start(mac);

        for (size_t i = 0; i < addr_count; i++) {
            const uint8_t addr = addrs[i];
            uint32_t id1 = 0;
            uint32_t id2 = 0;
            if (mac->read_phy_reg(mac, addr, 2, &id1) != ESP_OK) {
                continue;
            }
            if (mac->read_phy_reg(mac, addr, 3, &id2) != ESP_OK) {
                continue;
            }

            const uint16_t id1_16 = static_cast<uint16_t>(id1 & 0xFFFF);
            const uint16_t id2_16 = static_cast<uint16_t>(id2 & 0xFFFF);
            if (!pbEthLooksLikeValidPhyId(id1_16, id2_16)) {
                continue;
            }

            out = PbEthDetectedPhy{
                .clk_mode = clk_mode,
                .mdc_pin = mdc,
                .mdio_pin = mdio,
                .phy_addr = addr,
                .id1 = id1_16,
                .id2 = id2_16,
            };
            found = true;
            break;
        }

        (void)mac->stop(mac);
        (void)mac->deinit(mac);
    }

    (void)mac->del(mac);
    return found;
}

static bool pbEthDetectPhy(PbEthDetectedPhy &out) {
    // Try to make sure the most common PHY enable pin is on.
    pinMode(16, OUTPUT);
    digitalWrite(16, HIGH);
    delay(10);

    // Most designs strap PHY address in the low range, but scanning all 0..31 is cheap and avoids
    // getting stuck on unusual strap combinations.
    static uint8_t addr_list[32];
    static bool addr_list_init = false;
    if (!addr_list_init) {
        for (int i = 0; i < 32; i++) {
            addr_list[i] = static_cast<uint8_t>(i);
        }
        addr_list_init = true;
    }

    // Phase A: most common SMI pins on ESP32 Ethernet designs.
    static const uint8_t addr_short[] = {0, 1, 2, 3};
    static const int common_pairs[][2] = {
        {23, 18},
        {18, 23},
    };
    static const eth_clock_mode_t clocks_all[] = {
        ETH_CLOCK_GPIO0_IN,
        ETH_CLOCK_GPIO0_OUT,
        ETH_CLOCK_GPIO17_OUT,
        ETH_CLOCK_GPIO16_OUT,
    };
    for (eth_clock_mode_t clk : clocks_all) {
        for (const auto &pair : common_pairs) {
            if (pbEthMdioScanFirstHit(clk, pair[0], pair[1], addr_list, sizeof(addr_list) / sizeof(addr_list[0]), out)) {
                return true;
            }
        }
    }

    // Phase B: a few non-standard clones route MDIO/MDC to other pins.
    static const int extended_pairs[][2] = {
        // Some factory images/logs for ESP32-ETH01 show GPIO16/GPIO32/GPIO2 being configured
        // around Ethernet init. These combos cover that common vendor wiring.
        {16, 32},
        {32, 16},
        {16, 2},
        {2, 16},
        {32, 2},
        {2, 32},
        {23, 32},
        {32, 23},
        {18, 32},
        {32, 18},
        {23, 2},
        {2, 23},
        {18, 2},
        {2, 18},
        {23, 16},
        {16, 23},
        {23, 17},
        {17, 23},
        {18, 16},
        {16, 18},
        {18, 17},
        {17, 18},
        {23, 5},
        {5, 23},
        {18, 5},
        {5, 18},
        {33, 32},
        {32, 33},
    };
    static const eth_clock_mode_t clocks_some[] = {
        ETH_CLOCK_GPIO0_IN,
        ETH_CLOCK_GPIO17_OUT,
        ETH_CLOCK_GPIO0_OUT,
    };
    for (eth_clock_mode_t clk : clocks_some) {
        for (const auto &pair : extended_pairs) {
            if (pbEthMdioScanFirstHit(clk, pair[0], pair[1], addr_list, sizeof(addr_list) / sizeof(addr_list[0]), out)) {
                return true;
            }
        }
    }

#if PB_ETH_AUTOCONFIG_DETECT_WIDE
    // Phase C (wide): brute-force a wider set of safe-ish GPIOs for MDC/MDIO, but scan only addr 0..3
    // (most modules strap the PHY in that range). This is slow-ish, so keep it behind a flag.
    Serial.println("🔎 MDIO detect (wide): перебираю додаткові варіанти MDC/MDIO (може зайняти до ~30-60 сек)...");
    static const int candidate_pins[] = {
        23, 18, 16, 32, 2, 5, 4, 12, 13, 14, 15, 17, 33,
    };
    static const eth_clock_mode_t clocks_wide[] = {
        ETH_CLOCK_GPIO0_IN,
        ETH_CLOCK_GPIO17_OUT,
        ETH_CLOCK_GPIO0_OUT,
        ETH_CLOCK_GPIO16_OUT,
    };
    for (eth_clock_mode_t clk : clocks_wide) {
        for (int mdc : candidate_pins) {
            // Avoid obvious conflicts: clock pin cannot also be used for SMI.
            if ((clk == ETH_CLOCK_GPIO16_OUT && mdc == 16) || (clk == ETH_CLOCK_GPIO17_OUT && mdc == 17)) {
                continue;
            }
            for (int mdio : candidate_pins) {
                if (mdc == mdio) {
                    continue;
                }
                if ((clk == ETH_CLOCK_GPIO16_OUT && mdio == 16) || (clk == ETH_CLOCK_GPIO17_OUT && mdio == 17)) {
                    continue;
                }
                if (pbEthMdioScanFirstHit(clk,
                                          mdc,
                                          mdio,
                                          addr_short,
                                          sizeof(addr_short) / sizeof(addr_short[0]),
                                          out)) {
                    return true;
                }
            }
        }
    }
#endif

    return false;
}

static constexpr size_t PB_ETH_DYNAMIC_MAX = 24;
static PbEthProfile pb_eth_dynamic_profiles[PB_ETH_DYNAMIC_MAX];
static char pb_eth_dynamic_labels[PB_ETH_DYNAMIC_MAX][96];
static size_t pb_eth_dynamic_count;

static void pbEthBuildDynamicProfiles(int mdc, int mdio, uint8_t phy_addr) {
    pb_eth_dynamic_count = 0;

    auto add = [&](eth_clock_mode_t clk, int reset_pin, int pwr_en_pin, int pwr_en_level, int pwr_en_delay_ms) {
        if (pb_eth_dynamic_count >= PB_ETH_DYNAMIC_MAX) {
            return;
        }

        const size_t i = pb_eth_dynamic_count;
        snprintf(pb_eth_dynamic_labels[i],
                 sizeof(pb_eth_dynamic_labels[i]),
                 "det-mdc%d-mdio%d-addr%u-%s-rst%d-pwr%d_%d_%d",
                 mdc,
                 mdio,
                 static_cast<unsigned>(phy_addr),
                 pbEthClockModeStr(clk),
                 reset_pin,
                 pwr_en_pin,
                 pwr_en_level,
                 pwr_en_delay_ms);

        pb_eth_dynamic_profiles[i] = PbEthProfile{
            .label = pb_eth_dynamic_labels[i],
            .phy_addr = phy_addr,
            .reset_pin = reset_pin,
            .mdc_pin = mdc,
            .mdio_pin = mdio,
            .phy_type = ETH_PHY_LAN8720,
            .clk_mode = clk,
            .pwr_en_pin = pwr_en_pin,
            .pwr_en_level = pwr_en_level,
            .pwr_en_delay_ms = pwr_en_delay_ms,
        };

        pb_eth_dynamic_count++;
    };

    // Most likely first: external clock on GPIO0, no reset/pwr toggles.
    add(ETH_CLOCK_GPIO0_IN, -1, -1, 1, 0);
    add(ETH_CLOCK_GPIO0_IN, -1, 16, 1, 250);
    add(ETH_CLOCK_GPIO0_IN, 5, -1, 1, 0);
    add(ETH_CLOCK_GPIO0_IN, 5, 16, 1, 250);
    add(ETH_CLOCK_GPIO0_IN, 16, -1, 1, 0);
    add(ETH_CLOCK_GPIO0_IN, 16, 16, 1, 250);

    // Internal clock out variants.
    add(ETH_CLOCK_GPIO17_OUT, -1, -1, 1, 0);
    add(ETH_CLOCK_GPIO17_OUT, -1, 16, 1, 250);
    add(ETH_CLOCK_GPIO17_OUT, 5, -1, 1, 0);
    add(ETH_CLOCK_GPIO17_OUT, 5, 16, 1, 250);
    add(ETH_CLOCK_GPIO17_OUT, 16, -1, 1, 0);
    add(ETH_CLOCK_GPIO17_OUT, 16, 16, 1, 250);

    add(ETH_CLOCK_GPIO0_OUT, -1, -1, 1, 0);
    add(ETH_CLOCK_GPIO0_OUT, -1, 16, 1, 250);

    // Last resort: active-low power enable on GPIO16 (some clones).
    add(ETH_CLOCK_GPIO0_IN, -1, 16, 0, 250);
    add(ETH_CLOCK_GPIO17_OUT, -1, 16, 0, 250);
}

// A small set of "known good" profiles for ESP32-ETH01 clones.
// We intentionally keep this list short: each failed ETH.begin() leaks memory in Arduino-ESP32,
// so we try one profile per boot and reboot to move to the next.
static const PbEthProfile PB_ETH_PROFILES[] = {
    // Baseline: don't touch RESET/PWR_EN, just try the most common wiring first.
    { "extclk-gpio0_in-addr0", 0, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1", 1, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr2", 2, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr3", 3, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr0-mdc18-mdio23", 0, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-mdc18-mdio23", 1, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr2-mdc18-mdio23", 2, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr3-mdc18-mdio23", 3, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },

    // Some factory firmwares for ESP32-ETH01 configure GPIO16/GPIO32 around Ethernet init.
    // These profiles cover that common alternative SMI wiring.
    { "extclk-gpio0_in-addr0-mdc16-mdio32", 0, -1, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-mdc16-mdio32", 1, -1, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr0-mdc16-mdio32-reset5", 0, 5, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-mdc16-mdio32-reset5", 1, 5, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "intclk-gpio17_out-addr0-mdc16-mdio32", 0, -1, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr1-mdc16-mdio32", 1, -1, 16, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },

    // Another common pair seen in vendor examples: MDC=GPIO16, MDIO=GPIO2 (or vice versa).
    // Note: avoid ETH_CLOCK_GPIO16_OUT here because it conflicts with MDC=16.
    { "extclk-gpio0_in-addr0-mdc16-mdio2", 0, -1, 16, 2, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-mdc16-mdio2", 1, -1, 16, 2, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "intclk-gpio17_out-addr0-mdc16-mdio2", 0, -1, 16, 2, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr1-mdc16-mdio2", 1, -1, 16, 2, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },

    // Variants where MDIO is on GPIO32 and MDC stays on GPIO23.
    { "extclk-gpio0_in-addr0-mdc23-mdio32", 0, -1, 23, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-mdc23-mdio32", 1, -1, 23, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "intclk-gpio17_out-addr0-mdc23-mdio32", 0, -1, 23, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr1-mdc23-mdio32", 1, -1, 23, 32, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },

    // ESP32-Ethernet-Kit-like wiring: PHY reset on GPIO5.
    // This shows up on some ESP32-ETH01 clones as well.
    { "extclk-gpio0_in-addr0-reset5", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-reset5", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },

    // Rare clones swap MDC/MDIO. Cheap to try and it specifically fixes
    // "lan87xx_pwrctl: power up timeout" when LINK/ACT LEDs look normal.
    { "extclk-gpio0_in-addr0-reset5-mdc18-mdio23", 0, 5, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-reset5-mdc18-mdio23", 1, 5, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr0-reset5-pwren16_hi-mdc18-mdio23", 0, 5, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr1-reset5-pwren16_hi-mdc18-mdio23", 1, 5, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr0-pwren16_hi-mdc18-mdio23", 0, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr1-pwren16_hi-mdc18-mdio23", 1, -1, 18, 23, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },

    // External 50MHz clock fed into GPIO0 (EXT IN), often enabled by GPIO16 (PWR_EN).
    { "extclk-gpio0_in-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-gpio0_in-addr0-reset16", 0, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr1-reset16", 1, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, -1, 1, 0 },
    { "extclk-gpio0_in-addr0-reset16-pwren16_hi", 0, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },

    // ESP32 outputs 50MHz to PHY (no external clock): GPIO0_OUT or GPIO17_OUT.
    { "intclk-gpio0_out-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-addr0-reset16", 0, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, -1, 1, 0 },
    { "intclk-gpio0_out-addr1-reset16", 1, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr0-reset16", 0, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr1-reset16", 1, 16, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, -1, 1, 0 },
    { "intclk-gpio17_out-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    // Another clock-out option supported by ESP32 EMAC.
    { "intclk-gpio16_out-addr0", 0, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr1", 1, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr2", 2, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr3", 3, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr0-reset5", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr1-reset5", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, -1, 1, 0 },
    { "intclk-gpio16_out-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, 16, 1, 250 },
    { "intclk-gpio16_out-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO16_OUT, 16, 1, 250 },

    // Some boards have PWR_EN active low.
    { "extclk-gpio0_in-addr1-pwren16_lo", 1, -1, 23, 18, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN, 16, 0, 250 },

    // Alternative PHY types observed on ESP32-ETH01 clones.
    { "extclk-ip101-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr2-pwren16_hi", 2, -1, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr3-pwren16_hi", 3, -1, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr2-reset5-pwren16_hi", 2, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr3-reset5-pwren16_hi", 3, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "intclk-gpio0_out-ip101-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-ip101-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-ip101-addr2-reset5-pwren16_hi", 2, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio0_out-ip101-addr3-reset5-pwren16_hi", 3, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO0_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr0-reset5-pwren16_hi", 0, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr1-reset5-pwren16_hi", 1, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr2-reset5-pwren16_hi", 2, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr3-reset5-pwren16_hi", 3, 5, 23, 18, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    // Some clones swap MDC/MDIO (rare, but cheap to try).
    { "extclk-ip101-addr0-pwren16_hi-mdc18-mdio23", 0, -1, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr1-pwren16_hi-mdc18-mdio23", 1, -1, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr2-pwren16_hi-mdc18-mdio23", 2, -1, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ip101-addr3-pwren16_hi-mdc18-mdio23", 3, -1, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr0-reset5-pwren16_hi-mdc18-mdio23", 0, 5, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr1-reset5-pwren16_hi-mdc18-mdio23", 1, 5, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr2-reset5-pwren16_hi-mdc18-mdio23", 2, 5, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "intclk-gpio17_out-ip101-addr3-reset5-pwren16_hi-mdc18-mdio23", 3, 5, 18, 23, ETH_PHY_IP101, ETH_CLOCK_GPIO17_OUT, 16, 1, 250 },
    { "extclk-rtl8201-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_RTL8201, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-rtl8201-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_RTL8201, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ksz8081-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_KSZ8081, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ksz8081-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_KSZ8081, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ksz8041-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_KSZ8041, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-ksz8041-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_KSZ8041, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-dp83848-addr0-pwren16_hi", 0, -1, 23, 18, ETH_PHY_DP83848, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
    { "extclk-dp83848-addr1-pwren16_hi", 1, -1, 23, 18, ETH_PHY_DP83848, ETH_CLOCK_GPIO0_IN, 16, 1, 250 },
};

static int pbEthLoadPreferredProfileIndex() {
    Preferences prefs;
    // Open in RW mode so the namespace can be created automatically on first boot
    // (otherwise nvs_open fails with NOT_FOUND in read-only mode).
    if (!prefs.begin("pb_eth", false)) {
        return -1;
    }
    const uint32_t ver = prefs.getUInt("cfg_ver", 0);
    if (ver != PB_ETH_PROFILESET_VERSION) {
        prefs.end();
        return -1;
    }
    const uint8_t idx = prefs.getUChar("cfg_idx", 0xFF);
    prefs.end();
    return (idx == 0xFF) ? -1 : static_cast<int>(idx);
}

static void pbEthStorePreferredProfileIndex(uint8_t idx) {
    Preferences prefs;
    if (!prefs.begin("pb_eth", false)) {
        return;
    }
    prefs.putUInt("cfg_ver", PB_ETH_PROFILESET_VERSION);
    prefs.putUChar("cfg_idx", idx);
    prefs.end();
}

static int pbEthFindFirstProfileByPhyType(eth_phy_type_t type) {
    const size_t profile_count = sizeof(PB_ETH_PROFILES) / sizeof(PB_ETH_PROFILES[0]);
    for (size_t i = 0; i < profile_count; i++) {
        if (PB_ETH_PROFILES[i].phy_type == type) {
            return static_cast<int>(i);
        }
    }
    return -1;
}
#endif

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
    Serial.printf("  Section:  %d\n", SECTION_ID);
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
    Serial.println("🔌 Ініціалізація Ethernet PHY (RMII)...");

    eth_connected = false;

#if PB_ETH_AUTOCONFIG
    // Many ESP32-ETH01 clones require GPIO16 to be driven HIGH to power up (or de-assert reset for) the PHY.
    // Factory firmwares often do this very early. Keep it stable across autoconfig reboots.
    pinMode(16, OUTPUT);
    digitalWrite(16, HIGH);
    delay(10);

    // Some vendor firmwares configure these as pulled-up inputs ("CFG" straps / options).
    // This is harmless for typical boards and avoids floating pins on some revisions.
    pinMode(2, INPUT_PULLUP);
    pinMode(32, INPUT_PULLUP);

    const PbEthProfile *profiles = PB_ETH_PROFILES;
    size_t profile_count = sizeof(PB_ETH_PROFILES) / sizeof(PB_ETH_PROFILES[0]);

    const bool session_mismatch = (pb_eth_magic != PB_ETH_AUTOCONFIG_MAGIC ||
                                   pb_eth_profileset_ver != PB_ETH_PROFILESET_VERSION ||
                                   pb_eth_detect_done > 1 ||
                                   pb_eth_detect_valid > 1 ||
                                   pb_eth_profile_source > 1);
    if (session_mismatch) {
        pb_eth_magic = PB_ETH_AUTOCONFIG_MAGIC;
        pb_eth_profileset_ver = PB_ETH_PROFILESET_VERSION;
        pb_eth_next_profile = 0;
        pb_eth_tried_count = 0;
        pb_eth_detect_done = 0;
        pb_eth_detect_valid = 0;
        pb_eth_detect_mdc = -1;
        pb_eth_detect_mdio = -1;
        pb_eth_detect_addr = 0xFF;
        pb_eth_profile_source = 0;
    }

    // Run MDIO detection once per autoconfig session. This helps ESP32-ETH01 clones
    // where MDC/MDIO pins or PHY address differ from the common 23/18 + addr0/1.
    if (!pb_eth_detect_done) {
        pb_eth_detect_done = 1;

        PbEthDetectedPhy det{};
        if (pbEthDetectPhy(det)) {
            pb_eth_detect_valid = 1;
            pb_eth_detect_mdc = static_cast<int8_t>(det.mdc_pin);
            pb_eth_detect_mdio = static_cast<int8_t>(det.mdio_pin);
            pb_eth_detect_addr = det.phy_addr;
            pb_eth_profile_source = 1;
            Serial.printf("🔎 MDIO detect: PHY found (id=0x%04X/0x%04X) @addr=%u using mdc=%d mdio=%d clock=%s\n",
                          static_cast<unsigned>(det.id1),
                          static_cast<unsigned>(det.id2),
                          static_cast<unsigned>(det.phy_addr),
                          det.mdc_pin,
                          det.mdio_pin,
                          pbEthClockModeStr(det.clk_mode));
        } else {
            Serial.println("🔎 MDIO detect: не вдалося прочитати PHY ID на типових MDC/MDIO. Ймовірно, інші піни або проблема з лініями MDIO/MDC.");
            pb_eth_profile_source = 0;
            pb_eth_detect_valid = 0;
        }
    }

    if (pb_eth_profile_source == 1 && pb_eth_detect_valid && pb_eth_detect_mdc >= 0 && pb_eth_detect_mdio >= 0 && pb_eth_detect_addr != 0xFF) {
        pbEthBuildDynamicProfiles(pb_eth_detect_mdc, pb_eth_detect_mdio, pb_eth_detect_addr);
        if (pb_eth_dynamic_count > 0) {
            profiles = pb_eth_dynamic_profiles;
            profile_count = pb_eth_dynamic_count;
        } else {
            pb_eth_profile_source = 0;
            pb_eth_detect_valid = 0;
        }
    }

    int preferred = -1;
    int preferred_by_type = -1;
    if (pb_eth_profile_source == 0) {
        preferred = pbEthLoadPreferredProfileIndex();
        if (preferred < 0 || preferred >= static_cast<int>(profile_count)) {
            preferred = -1;
        }

        if (PB_ETH_AUTOCONFIG_PREFERRED_PHY != ETH_PHY_MAX) {
            preferred_by_type = pbEthFindFirstProfileByPhyType(static_cast<eth_phy_type_t>(PB_ETH_AUTOCONFIG_PREFERRED_PHY));
        }
    }

    const bool state_invalid = (pb_eth_next_profile >= profile_count || pb_eth_tried_count >= profile_count);
    if (session_mismatch || state_invalid) {
        pb_eth_tried_count = 0;
        if (pb_eth_profile_source == 0) {
            if (preferred >= 0) {
                pb_eth_next_profile = static_cast<uint8_t>(preferred);
            } else if (preferred_by_type >= 0) {
                pb_eth_next_profile = static_cast<uint8_t>(preferred_by_type);
            } else {
                pb_eth_next_profile = 0;
            }
        } else {
            pb_eth_next_profile = 0;
        }
    }

    const uint8_t idx = pb_eth_next_profile;
    const PbEthProfile &p = profiles[idx];

    const unsigned attempt_no = static_cast<unsigned>(pb_eth_tried_count) + 1;
    Serial.printf("🔧 ETH autoconfig: attempt %u/%u, profile %u: %s\n",
                  attempt_no,
                  static_cast<unsigned>(profile_count),
                  static_cast<unsigned>(idx + 1),
                  p.label);
    Serial.printf("   PHY_TYPE=%s, PHY_ADDR=%u, RESET=%d\n", pbEthPhyTypeStr(p.phy_type), p.phy_addr, p.reset_pin);
    Serial.printf("   MDC=%d, MDIO=%d\n", p.mdc_pin, p.mdio_pin);
    Serial.printf("   CLK_MODE=%s (%d)\n", pbEthClockModeStr(p.clk_mode), static_cast<int>(p.clk_mode));
    Serial.printf("   PWR_EN=%d (level=%d, delay=%dms)\n", p.pwr_en_pin, p.pwr_en_level, p.pwr_en_delay_ms);

    if (p.pwr_en_pin >= 0) {
        pinMode(p.pwr_en_pin, OUTPUT);
        digitalWrite(p.pwr_en_pin, p.pwr_en_level ? HIGH : LOW);
        if (p.pwr_en_delay_ms > 0) {
            delay(p.pwr_en_delay_ms);
        }
    }

    // Diagnostics: read raw PHY ID registers (2/3) before ETH.begin(). This helps distinguish:
    // - wrong PHY address (often 0xFFFF/0xFFFF)
    // - wrong MDC/MDIO pins (read fails)
    // - real PHY present (valid OUI/model)
    {
        // If we have a dedicated RESET pin in this profile, make sure it's not stuck low
        // before reading PHY IDs (most PHY reset pins are active-low).
        if (p.reset_pin >= 0 && p.reset_pin != p.pwr_en_pin) {
            pinMode(p.reset_pin, OUTPUT);
            digitalWrite(p.reset_pin, HIGH);
            delay(10);
        }

        uint16_t id1 = 0;
        uint16_t id2 = 0;
        const bool id_ok = pbEthMdioReadPhyIdRaw(p.clk_mode, p.mdc_pin, p.mdio_pin, p.phy_addr, id1, id2);
        if (id_ok) {
            Serial.printf("   PHY_ID=0x%04X/0x%04X\n", static_cast<unsigned>(id1), static_cast<unsigned>(id2));
        } else {
            Serial.println("   PHY_ID=<read failed>");
        }
    }

    if (!ETH.begin(p.phy_addr, p.reset_pin, p.mdc_pin, p.mdio_pin, p.phy_type, p.clk_mode)) {
        Serial.println("❌ ETH.begin() не вдалося (PHY не відповідає).");

        pb_eth_tried_count++;
        pb_eth_next_profile = static_cast<uint8_t>((idx + 1) % profile_count);

        if (pb_eth_tried_count >= profile_count) {
            Serial.println("❌ ETH autoconfig: жоден профіль не підійшов.");
            Serial.println("   Найчастіші причини:");
            Serial.println("   - неправильний RMII clock mode (IN/OUT) або pin");
            Serial.println("   - інший PHY type (LAN8720 vs IP101/RTL8201)");
            Serial.println("   - PHY не має живлення/завис у reset");
            Serial.println("   Діагностика (для ESP32-ETH01 клонів):");
            Serial.println("   - встав кабель у свіч: має світитись LINK/ACT на RJ45");
            Serial.println("   - мультиметром поміряй IO16->GND під час старту (має бути ~3.3V, якщо це PWR_EN)");
            Serial.println("   - перевір, чи є на платі 50MHz oscillator і чи він не припаяний 'навпаки' (є такі заводські дефекти)");

            // If we were using detected/dynamic profiles and still failed, fall back once to the generic list.
            if (pb_eth_profile_source == 1) {
                Serial.println("↻ Fallback: переключаюсь на загальний список профілів і перезавантажуюсь...");
                pb_eth_profile_source = 0;
                pb_eth_detect_valid = 0;
                pb_eth_next_profile = 0;
                pb_eth_tried_count = 0;
                delay(1500);
                Serial.flush();
                ESP.restart();
            }
            return;
        }

        Serial.printf("↻ ETH autoconfig: reboot для наступного профілю (%u/%u)...\n",
                      static_cast<unsigned>(pb_eth_next_profile + 1),
                      static_cast<unsigned>(profile_count));
        delay(1500);
        Serial.flush();
        ESP.restart();
        return;
    }

    // We got a working low-level init; remember this profile for next boots.
    if (pb_eth_profile_source == 0 && preferred != static_cast<int>(idx)) {
        pbEthStorePreferredProfileIndex(idx);
    }
    pb_eth_tried_count = 0;
    pb_eth_next_profile = idx;
#else
    Serial.printf("   PHY_ADDR=%d, RESET=%d\n", PB_ETH_PHY_ADDR, PB_ETH_PHY_POWER);
    Serial.printf("   MDC=%d, MDIO=%d\n", PB_ETH_PHY_MDC, PB_ETH_PHY_MDIO);
    Serial.printf("   CLK_MODE=%s (%d)\n", pbEthClockModeStr(PB_ETH_CLK_MODE), static_cast<int>(PB_ETH_CLK_MODE));
    Serial.printf("   PWR_EN=%d (level=%d, delay=%dms)\n",
                  PB_ETH_POWER_ENABLE_PIN,
                  PB_ETH_POWER_ENABLE_LEVEL,
                  PB_ETH_POWER_UP_DELAY_MS);

    if (PB_ETH_POWER_ENABLE_PIN >= 0) {
        pinMode(PB_ETH_POWER_ENABLE_PIN, OUTPUT);
        digitalWrite(PB_ETH_POWER_ENABLE_PIN, PB_ETH_POWER_ENABLE_LEVEL ? HIGH : LOW);
        delay(PB_ETH_POWER_UP_DELAY_MS);
    }

    if (!ETH.begin(PB_ETH_PHY_ADDR,
                   PB_ETH_PHY_POWER,
                   PB_ETH_PHY_MDC,
                   PB_ETH_PHY_MDIO,
                   PB_ETH_PHY_TYPE,
                   PB_ETH_CLK_MODE)) {
        Serial.println("❌ Помилка запуску Ethernet!");
        return;
    }
#endif

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
