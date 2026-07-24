// Template. Copy to config.h and fill in your values (config.h is gitignored).
#pragma once

// WiFi settings
#define WIFI_SSID      "YOUR_SSID"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

// MQTT broker = Home Assistant's Mosquitto add-on.
// Create this user in Mosquitto (see MQTT_SETUP.md).
#define MQTT_HOST      "homeassistant.local"
#define MQTT_PORT      1883
#define MQTT_USER      "humsienk_cyd"
#define MQTT_PASSWORD  "YOUR_MQTT_PASSWORD"
#define MQTT_BASE      "humsienk"
#define HA_DISCOVERY_PREFIX "homeassistant"

// Batteries to read, by BLE name (= serial = QR). Label is shown on-screen/in HA.
// List 1, 2, or more — the firmware scales automatically. For 4+ batteries also
// raise CONFIG_BT_NIMBLE_MAX_CONNECTIONS in platformio.ini to >= your count.
struct BatteryCfg { const char* label; const char* name; };
static const BatteryCfg BATTERIES[] = {
  { "LEFT",  "HS0000000000000000" },
  { "RIGHT", "HS0000000000000001" },
  // { "BAT3", "HS0000000000000002" },   // add more as needed
};
static const int NUM_BATTERIES = sizeof(BATTERIES) / sizeof(BATTERIES[0]);
