// HumsiENK standalone battery monitor for the CYD (ESP32-2432S028R).
// Reads both batteries over BLE (WATT/HiLink), shows them on the TFT, and
// publishes to Home Assistant via MQTT discovery.
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <NimBLEDevice.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#include "config.h"
#include "watt.h"

// ----------------------------------------------------------- display (CYD)
class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9341 _panel;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
 public:
  LGFX() {
    { auto c = _bus.config();
      c.spi_host = HSPI_HOST; c.spi_mode = 0; c.freq_write = 40000000; c.freq_read = 16000000;
      c.spi_3wire = false; c.use_lock = true; c.dma_channel = SPI_DMA_CH_AUTO;
      c.pin_sclk = 14; c.pin_mosi = 13; c.pin_miso = 12; c.pin_dc = 2;
      _bus.config(c); _panel.setBus(&_bus); }
    { auto c = _panel.config();
      c.pin_cs = 15; c.pin_rst = -1; c.pin_busy = -1;
      c.panel_width = 240; c.panel_height = 320; c.offset_rotation = 0;
      c.readable = true; c.invert = false; c.rgb_order = false; c.bus_shared = false;
      _panel.config(c); }
    { auto c = _light.config(); c.pin_bl = 21; c.freq = 44100; c.pwm_channel = 7;
      _light.config(c); _panel.setLight(&_light); }
    setPanel(&_panel);
  }
};
static LGFX tft;
// Off-screen canvas for flicker-free drawing (render fully, then blit in one pass).
static LGFX_Sprite canvas(&tft);
static bool useCanvas = false;

// ----------------------------------------------------------- BLE globals
static NimBLEUUID SVC("0000fff0-0000-1000-8000-00805f9b34fb");
static NimBLEUUID CH_NOTIFY("0000fff1-0000-1000-8000-00805f9b34fb");
static NimBLEUUID CH_WRITE("0000fff2-0000-1000-8000-00805f9b34fb");
static NimBLEUUID CH_AUTH("0000fffa-0000-1000-8000-00805f9b34fb");

// Persistent per-battery connection state (connect once, then just re-poll ->
// fast refresh instead of scan+connect every cycle).
struct BatConn {
  NimBLEClient* client = nullptr;
  NimBLERemoteCharacteristic* wch = nullptr;
  uint8_t acc[512];
  size_t  accLen = 0;
  uint8_t frame[512];
  size_t  frameLen = 0;
  volatile bool gotFrame = false;
};
static BatConn conns[NUM_BATTERIES];

static void feedFrame(int idx, uint8_t* data, size_t len) {
  BatConn& c = conns[idx];
  if (c.accLen + len > sizeof(c.acc)) c.accLen = 0;      // overflow guard
  memcpy(c.acc + c.accLen, data, len); c.accLen += len;
  size_t s = 0; while (s < c.accLen && c.acc[s] != watt::HEAD_DEFAULT) s++;
  if (s > 0) { memmove(c.acc, c.acc + s, c.accLen - s); c.accLen -= s; }
  if (c.accLen >= 8) {
    size_t total = ((c.acc[6] << 8) | c.acc[7]) + 11;
    if (total <= sizeof(c.acc) && c.accLen >= total) {
      if (c.acc[total - 1] == watt::TAIL) {
        memcpy(c.frame, c.acc, total); c.frameLen = total; c.gotFrame = true;
      }
      memmove(c.acc, c.acc + total, c.accLen - total); c.accLen -= total;
    }
  }
}

// (Re)establish a held connection for battery idx: scan, connect, subscribe, auth.
static bool ensureConnected(int idx) {
  BatConn& c = conns[idx];
  if (c.client && c.client->isConnected()) return true;
  if (c.client) { NimBLEDevice::deleteClient(c.client); c.client = nullptr; c.wch = nullptr; }

  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setActiveScan(true); scan->setInterval(45); scan->setWindow(15);
  NimBLEScanResults res = scan->start(5, false);
  NimBLEAdvertisedDevice found; bool have = false;
  for (int i = 0; i < res.getCount(); i++) {
    NimBLEAdvertisedDevice d = res.getDevice(i);
    if (d.getName() == BATTERIES[idx].name) { found = d; have = true; break; }
  }
  scan->clearResults();
  if (!have) return false;

  NimBLEClient* cli = NimBLEDevice::createClient();
  if (!cli->connect(&found)) { NimBLEDevice::deleteClient(cli); return false; }
  NimBLERemoteService* svc = cli->getService(SVC);
  NimBLERemoteCharacteristic* nch = svc ? svc->getCharacteristic(CH_NOTIFY) : nullptr;
  NimBLERemoteCharacteristic* wch = svc ? svc->getCharacteristic(CH_WRITE) : nullptr;
  NimBLERemoteCharacteristic* ach = svc ? svc->getCharacteristic(CH_AUTH) : nullptr;
  if (!nch || !wch || !ach || !nch->canNotify()) {
    cli->disconnect(); NimBLEDevice::deleteClient(cli); return false;
  }
  c.accLen = 0; c.gotFrame = false;
  nch->subscribe(true, [idx](NimBLERemoteCharacteristic*, uint8_t* d, size_t l, bool) {
    feedFrame(idx, d, l);
  });
  ach->writeValue((uint8_t*)watt::AUTH_KEY, strlen(watt::AUTH_KEY), true);  // "HiLink"
  delay(150);
  c.client = cli; c.wch = wch;
  return true;
}

// Poll one battery over its held connection (fast: just write + await reply).
static bool readBattery(int idx, watt::Reading& out) {
  if (!ensureConnected(idx)) return false;
  BatConn& c = conns[idx];
  c.gotFrame = false; c.accLen = 0;
  uint8_t rf[11]; watt::build_read_frame(watt::DP_ANALOG_QUANTITY, rf);
  if (!c.wch->writeValue(rf, 11, false)) return false;
  unsigned long t0 = millis();
  while (millis() - t0 < 1500 && !c.gotFrame) delay(10);
  if (c.gotFrame) return watt::parse_analog_frame(c.frame, c.frameLen, out);
  return false;
}

// ----------------------------------------------------------- WiFi + MQTT
static WiFiClient wifiClient;
static PubSubClient mqtt(wifiClient);

static void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(250);
}

static void ensureMqtt() {
  if (mqtt.connected()) return;
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);
  String cid = "cyd-humsienk-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  String availAll = String(MQTT_BASE) + "/bridge/availability";
  if (strlen(MQTT_USER))
    mqtt.connect(cid.c_str(), MQTT_USER, MQTT_PASSWORD, availAll.c_str(), 0, true, "offline");
  else
    mqtt.connect(cid.c_str(), nullptr, nullptr, availAll.c_str(), 0, true, "offline");
  if (mqtt.connected()) mqtt.publish(availAll.c_str(), "online", true);
}

// One discovery sensor. state topic is JSON; value_template pulls `key`.
static void pubSensor(const char* serial, const char* label, const char* key,
                      const char* name, const char* unit, const char* dclass) {
  char uid[96]; snprintf(uid, sizeof(uid), "humsienk_%s_%s", serial, key);
  char topic[160]; snprintf(topic, sizeof(topic), "%s/sensor/%s/config", HA_DISCOVERY_PREFIX, uid);
  JsonDocument doc;
  doc["name"] = name;
  doc["uniq_id"] = uid;
  doc["obj_id"] = uid;
  char st[96]; snprintf(st, sizeof(st), "%s/%s/state", MQTT_BASE, serial);
  char av[96]; snprintf(av, sizeof(av), "%s/%s/availability", MQTT_BASE, serial);
  doc["stat_t"] = st;
  doc["avty_t"] = av;
  char vt[48]; snprintf(vt, sizeof(vt), "{{ value_json.%s }}", key);
  doc["val_tpl"] = vt;
  if (unit && unit[0]) doc["unit_of_meas"] = unit;
  if (dclass && dclass[0]) { doc["dev_cla"] = dclass; doc["stat_cla"] = "measurement"; }
  JsonObject dev = doc["dev"].to<JsonObject>();
  char devid[64]; snprintf(devid, sizeof(devid), "humsienk_%s", serial);
  dev["ids"][0] = devid;
  char dn[64]; snprintf(dn, sizeof(dn), "HumsiENK %s (%s)", label, serial);
  dev["name"] = dn;
  dev["mf"] = "HumsiENK / Shake World";
  dev["mdl"] = "48V 100Ah LiFePO4";
  char buf[900]; size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(topic, (const uint8_t*)buf, n, true);
}

static bool discovered[NUM_BATTERIES] = {false};

// Full engineer's readout -> Home Assistant (the CYD screen stays simple).
static void publishDiscovery(int idx, const char* serial, const char* label,
                             const watt::Reading& r) {
  if (discovered[idx]) return;
  pubSensor(serial, label, "soc", "SOC", "%", "battery");
  pubSensor(serial, label, "voltage", "Voltage", "V", "voltage");
  pubSensor(serial, label, "current", "Current", "A", "current");
  pubSensor(serial, label, "power", "Power", "W", "power");
  pubSensor(serial, label, "remaining_capacity", "Remaining Capacity", "Ah", "");
  pubSensor(serial, label, "total_capacity", "Total Capacity", "Ah", "");
  pubSensor(serial, label, "design_capacity", "Design Capacity", "Ah", "");
  pubSensor(serial, label, "cycles", "Cycles", "", "");
  pubSensor(serial, label, "mos_temp", "MOSFET Temp", "°C", "temperature");
  pubSensor(serial, label, "pcb_temp", "PCB Temp", "°C", "temperature");
  pubSensor(serial, label, "cell_min", "Cell Voltage Min", "V", "voltage");
  pubSensor(serial, label, "cell_max", "Cell Voltage Max", "V", "voltage");
  pubSensor(serial, label, "cell_delta", "Cell Voltage Delta", "V", "voltage");
  char key[20], name[28];
  for (int c = 1; c <= r.cellCount && c <= 32; c++) {
    snprintf(key, sizeof(key), "cell_%d", c);
    snprintf(name, sizeof(name), "Cell %d Voltage", c);
    pubSensor(serial, label, key, name, "V", "voltage");
    mqtt.loop();
  }
  for (int c = 1; c <= r.cellTempCount && c <= 16; c++) {
    snprintf(key, sizeof(key), "cell_temp_%d", c);
    snprintf(name, sizeof(name), "Cell Temp %d", c);
    pubSensor(serial, label, key, name, "°C", "temperature");
    mqtt.loop();
  }
  discovered[idx] = true;
}

static void publishState(const char* serial, const watt::Reading& r) {
  JsonDocument doc;
  doc["soc"] = r.soc;
  doc["voltage"] = roundf(r.voltage * 100) / 100.0;
  doc["current"] = roundf(r.current * 100) / 100.0;
  doc["power"] = roundf(r.power * 10) / 10.0;
  doc["remaining_capacity"] = roundf(r.remainingCapacity * 10) / 10.0;
  doc["total_capacity"] = roundf(r.totalCapacity * 10) / 10.0;
  doc["design_capacity"] = roundf(r.designCapacity * 10) / 10.0;
  doc["cycles"] = r.cycleNumber;
  doc["mos_temp"] = roundf(r.mosTemp * 10) / 10.0;
  doc["pcb_temp"] = roundf(r.pcbTemp * 10) / 10.0;
  doc["cell_min"] = roundf(r.cellMin * 1000) / 1000.0;
  doc["cell_max"] = roundf(r.cellMax * 1000) / 1000.0;
  doc["cell_delta"] = roundf(r.cellDelta * 1000) / 1000.0;
  char key[20];
  for (int c = 0; c < r.cellCount && c < 32; c++) {
    snprintf(key, sizeof(key), "cell_%d", c + 1);
    doc[key] = roundf(r.cells[c] * 1000) / 1000.0;
  }
  for (int c = 0; c < r.cellTempCount && c < 16; c++) {
    snprintf(key, sizeof(key), "cell_temp_%d", c + 1);
    doc[key] = roundf(r.cellTemps[c] * 10) / 10.0;
  }
  char buf[900]; size_t n = serializeJson(doc, buf, sizeof(buf));
  char st[96]; snprintf(st, sizeof(st), "%s/%s/state", MQTT_BASE, serial);
  char av[96]; snprintf(av, sizeof(av), "%s/%s/availability", MQTT_BASE, serial);
  mqtt.publish(av, "online", true);
  mqtt.publish(st, (const uint8_t*)buf, n, false);
}

// ----------------------------------------------------------- display render
// EcoFlow-style: one big SOC ring + big watts + plain status for the whole bank.
static uint16_t socColor(int soc) {
  if (soc <= 15) return TFT_RED;
  if (soc <= 35) return TFT_ORANGE;
  return tft.color565(0, 220, 90);   // friendly green
}

static void drawDashboard(lgfx::LGFXBase &g, int socAvg, float watts, float volts,
                          int online, int total) {
  g.fillScreen(TFT_BLACK);
  const int cx = 90, cy = 118, rO = 88, rI = 72;   // thin ring, big hole for the number

  // SOC ring: grey track + coloured fill from top, clockwise.
  g.fillArc(cx, cy, rO, rI, -90, 270, g.color565(40, 40, 40));
  int endA = -90 + (int)(3.6f * (socAvg < 0 ? 0 : socAvg > 100 ? 100 : socAvg));
  g.fillArc(cx, cy, rO, rI, -90, endA, socColor(socAvg));

  // Big SOC number in the middle of the ring, with a small "%" superscript.
  char b[24];
  snprintf(b, sizeof(b), "%d", socAvg);
  g.setTextColor(TFT_WHITE, TFT_BLACK);
  g.setFont(&fonts::Font4);
  int pctW = g.textWidth("%");
  g.setFont(&fonts::Font7);             // 7-segment look, like the EcoFlow
  int nw = g.textWidth(b);
  // Centre the number+"%" group inside the ring hole so neither touches the ring.
  int grpW = nw + 4 + pctW;
  int nx = cx - grpW / 2 + nw / 2;      // number centre
  g.setTextDatum(middle_center);
  g.drawString(b, nx, cy);
  g.setFont(&fonts::Font4);
  g.setTextDatum(middle_left);
  g.drawString("%", nx + nw / 2 + 4, cy - 12);   // superscript, upper-right

  // Right side: charging status + big watts.
  const int rx = 246;                    // centre of right column (clears the ring)
  // Solar generator: on-battery is a given, so just charging vs discharging.
  // (BMS coulomb counter has a ~200 W deadband, so small loads read 0 -> IDLE.)
  bool charging = watts > 20;
  bool discharging = watts < -20;
  uint16_t green = g.color565(0, 220, 90);
  uint16_t stColor = charging ? green : discharging ? TFT_ORANGE : g.color565(180, 180, 180);
  const char* stWord = charging ? "CHARGING" : discharging ? "DISCHARGING" : "IDLE";

  g.setFont(&fonts::Font4);              // status word
  int sw = g.textWidth(stWord);
  int sx = rx;
  if (sx + sw / 2 > 316) sx = 316 - sw / 2;   // shift left if it would clip the edge
  g.setTextDatum(middle_center);
  g.setTextColor(stColor, TFT_BLACK);
  g.drawString(stWord, sx, 40);

  g.setFont(&fonts::Font7);
  g.setTextColor(TFT_WHITE, TFT_BLACK);
  snprintf(b, sizeof(b), "%d", (int)(fabsf(watts) + 0.5f));
  g.drawString(b, rx, 118);
  g.setFont(&fonts::Font4);
  g.drawString("WATTS", rx, 160);

  // Bottom strip: voltage + battery health.
  g.setFont(&fonts::Font2);
  g.setTextDatum(bottom_left);
  g.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  snprintf(b, sizeof(b), "%.1f V", volts);
  g.drawString(b, 8, g.height() - 6);
  g.setTextDatum(bottom_right);
  if (online == total) {
    g.setTextColor(g.color565(0, 200, 80), TFT_BLACK);
    snprintf(b, sizeof(b), "%d batteries OK", total);
  } else {
    g.setTextColor(TFT_RED, TFT_BLACK);
    snprintf(b, sizeof(b), "%d of %d online", online, total);
  }
  g.drawString(b, g.width() - 8, g.height() - 6);
}

// Render the dashboard, double-buffered if the canvas allocated (no flicker).
static void showDashboard(int socAvg, float watts, float volts, int online, int total) {
  if (useCanvas) {
    drawDashboard(canvas, socAvg, watts, volts, online, total);
    canvas.pushSprite(0, 0);
  } else {
    drawDashboard(tft, socAvg, watts, volts, online, total);
  }
}

// ----------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(1);           // landscape 320x240
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(middle_center);
  tft.setFont(&fonts::Font4);
  tft.setTextColor(TFT_WHITE);
  tft.drawString("HumsiENK monitor", tft.width() / 2, tft.height() / 2);

  NimBLEDevice::init("cyd-humsienk");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  ensureWifi();
  ensureMqtt();

  // Allocate the off-screen canvas AFTER WiFi/BLE claim their heap. A full 16bpp
  // buffer (150KB) doesn't fit alongside WiFi+BLE, so fall back to 8bpp (75KB).
  canvas.setColorDepth(16);
  useCanvas = canvas.createSprite(tft.width(), tft.height());
  if (!useCanvas) {
    canvas.setColorDepth(8);        // RGB332 — plenty for this UI
    useCanvas = canvas.createSprite(tft.width(), tft.height());
  }
  Serial.printf("[disp] double-buffer=%d depth=%d free heap=%u\n",
                useCanvas, useCanvas ? (int)canvas.getColorDepth() : 0, ESP.getFreeHeap());
}

void loop() {
  // Daily reboot: cheap insurance against slow heap/BLE/WiFi drift over weeks.
  if (millis() > 86400000UL) ESP.restart();

  ensureWifi();
  ensureMqtt();
  mqtt.loop();

  // Screen refreshes every loop (~2s); MQTT publishes less often to avoid spam.
  static unsigned long lastPub = 0;
  bool doPublish = mqtt.connected() && (millis() - lastPub > 5000);

  int online = 0, socSum = 0;
  float wattSum = 0, vSum = 0;
  for (int i = 0; i < NUM_BATTERIES; i++) {
    watt::Reading r;
    bool ok = readBattery(i, r);
    Serial.printf("[%s] %s\n", BATTERIES[i].label,
                  ok ? (String(r.voltage, 2) + "V " + String(r.power, 0) + "W SOC " + r.soc + "%").c_str()
                     : "offline");
    if (ok) {
      online++; socSum += r.soc; wattSum += r.power; vSum += r.voltage;
      if (doPublish) {
        publishDiscovery(i, BATTERIES[i].name, BATTERIES[i].label, r);
        publishState(BATTERIES[i].name, r);
      }
    } else if (doPublish) {
      char av[96]; snprintf(av, sizeof(av), "%s/%s/availability", MQTT_BASE, BATTERIES[i].name);
      mqtt.publish(av, "offline", true);
    }
    mqtt.loop();
  }
  if (doPublish) lastPub = millis();

  int   socAvg = online ? socSum / online : 0;
  float volts  = online ? vSum / online : 0.0f;
  // Repaint only when a displayed value actually changes -> no constant-refresh
  // strobe. Idle screen stays perfectly still.
  static int lastSoc = -1, lastW = 0x7fffffff, lastVx10 = -1, lastOnline = -1;
  int wDisp = (int)lroundf(wattSum);
  int vx10  = (int)lroundf(volts * 10);
  if (socAvg != lastSoc || wDisp != lastW || vx10 != lastVx10 || online != lastOnline) {
    showDashboard(socAvg, wattSum, volts, online, NUM_BATTERIES);
    lastSoc = socAvg; lastW = wDisp; lastVx10 = vx10; lastOnline = online;
  }
  delay(1200);   // fast poll; screen only repaints on change
}
