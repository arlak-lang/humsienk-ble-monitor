# HumsiENK BMS → Home Assistant & CYD Monitor — Project Scope

_Last updated: 2026-07-23_

## 1. Vision & intent

Build a **custom, low-cost, self-hosted solar-monitoring experience** for a home solar
generator — and in doing so **eliminate the Raspberry Pi + Solar Assistant** dependency
(Pis are expensive right now, and a commercial appliance experience like EcoFlow's is what
the household actually wants to *see*).

Two distinct audiences, one system:

- **The household / parents (consumer):** a dead-simple, glanceable panel — big battery %,
  big watts, charging/discharging. No jargon. It should feel like the EcoFlow they keep
  wanting to buy.
- **The owner (engineer):** the *full* readout in Home Assistant — every cell voltage,
  every temperature, capacities, cycles — for diagnostics and automation.

The guiding principle: **the display stays dumb-simple; Home Assistant gets everything.**

## 2. The physical system

- **Battery bank:** 2× **HumsiENK 48 V 100 Ah LiFePO₄** (51.2 V nominal, 16S, 5.12 kWh each →
  ~10 kWh total), in **parallel** on a 48 V bus, installed in the garage.
  - Manufacturer: Shenzhen Shake World New Energy Technology Co., Ltd.
  - BLE module: **HopeRF** (`HP-BLE-1.0` / `HPZXGT01-C-V1.1`).
  - Also expose a wired **RS485 / CAN / UART** port (currently unused).
- **Inverter/charger:** an **ANENJI ANJ-12000W-LVP-WIFI** (12 kW split-phase hybrid; its WiFi dongle is cloud-first, so local data comes from the serial/Modbus port). **Not yet wired to the batteries** — connecting it
  (power + closed-loop BMS comms) is a project goal.
- **Home Assistant:** runs as a systemd-supervised **QEMU VM at `homeassistant.local`** on the
  Debian laptop (bridged over `br0` on the wired NIC).
- **Displays:** "**CYD**" boards (Cheap Yellow Display, ESP32-2432S028R — ESP32 + 2.8"
  320×240 ILI9341 touchscreen). One drives this battery monitor; a sibling project
  ("**sundial**") is a CYD front-end for Solar Assistant that this effort aims to supersede.

## 3. Goals

| # | Goal | Status |
|---|------|--------|
| 1 | Read battery SOC/V/I/temp/cells over BLE **without the phone app** | ✅ Done |
| 2 | Standalone **parent-friendly CYD display** (no laptop needed) | ✅ Done |
| 3 | Publish **full detail to Home Assistant** via MQTT discovery | ✅ Done (pending broker) |
| 4 | Eliminate **Solar Assistant + Raspberry Pi** for battery data | ✅ Achieved for batteries |
| 5 | Read the **ANENJI inverter directly** (solar/load/energy) | 🔜 Future |
| 6 | Wire the inverter to the batteries (power + BMS comms) | 🔜 Future |

## 4. Architecture

```
 ┌────────────┐   BLE (WATT/HiLink, "HiLink" auth)   ┌─────────────┐   WiFi / MQTT   ┌──────────────┐
 │ 2× HumsiENK │◀───────────────────────────────────▶│  CYD (ESP32) │────────────────▶│ Home Assistant│
 │  batteries  │                                      │  reads both  │  HA discovery   │  (full detail)│
 └────────────┘                                      │  + TFT screen│                 └──────────────┘
        ▲                                             └──────┬──────┘
        │ RS485/CAN (future, wired)                          │ ILI9341 TFT
        │                                                    ▼
 ┌────────────┐  (future)                            "parents" glanceable panel
 │ ANENJI inverter│  Modbus → solar/load/energy → HA     (big % ring, watts, status)
 └────────────┘
```

The CYD is **fully self-contained**: it reads both batteries directly over BLE, renders the
simple panel, and publishes to HA. No Raspberry Pi, no Solar Assistant, no always-on laptop.

## 5. Protocol summary (the hard-won part)

The batteries speak the **WATT / "HiLink" protocol**, reverse-engineered by decompiling the
official Android app (`uni.UNI3890CA7` = `com.humsienk.hskpower`; protocol lib
`com.gz.wattcycle`; uses FastBLE). The "HS…" name suggested the documented *BMC* protocol
(aiobmsble) — that was a **red herring**; the app selects protocol by GATT UUIDs, and ours
match the **WATT** device type.

- **Identity:** BLE **name = serial number = QR code** (unique per battery; the address is
  also stable/public). The MAC the app shows (`26:7B:88:A0:63:A1`) is a bogus firmware default.
  - Left `HS0000000000000000` (`AA:BB:CC:00:00:01`), Right `HS0000000000000001` (`AA:BB:CC:00:00:02`)
- **Transport:** service `fff0`; **notify `fff1`**, **write `fff2`**, **auth `fffa`**.
- **Unlock (critical):** enable notify on `fff1`, then write ASCII **`"HiLink"`** to `fffa`.
  Without this the BMS ignores everything. No user password needed for reads.
- **Framing (Modbus/Tuya, big-endian):**
  `READ  7E 00 01 03 <addr:u16> <count:u16> <crc16-modbus> 0D`
  `REPLY 7E ver addr func <startAddr:u16> <len:u16> <payload> <crc16> 0D`
- **Real-time data = read data-point 140 (`0x8C`).** Payload: cellCount(u8), cells(u16/1000 V),
  tempCount(u8), mos/pcb temp `(u16-2730)/10 °C`, cell temps, current(14-bit mag, bit15 sign,
  bit14 ÷10), voltage(u16/100 V), remain/total/design capacity(u16/10 Ah), cycles(u16), SOC(u16).

### Behavioural gotchas (all handled, but document them)
- **Sparse advertising:** batteries advertise only in brief bursts; when **connected they stop
  advertising entirely** → while the CYD holds them, the phone app **cannot** connect (unplug
  the CYD to use the app). Confirmed live.
- **Coulomb-counter deadband:** the BMS reports **0 current/power below ~200 W** (a Dell R520
  didn't register). So sub-200 W flows show as `IDLE / 0 W` on the panel. For accurate low-power
  numbers we'd use the inverter (future).
- **Current sign:** validated — BMS **negative = discharging** (panel shows DISCHARGING
  correctly under real load). Opposite raw sign from the inverter, but the displayed state is right.
- **Capacity scaling — TO VERIFY:** raw reads 100 → app's `/10` = 10.0 Ah on a 100 Ah battery.
  Confirm the divider against the app before trusting capacity in HA.

## 6. Firmware (CYD) design

- **PlatformIO / Arduino-ESP32**, libs: NimBLE-Arduino (BLE central), LovyanGFX (display),
  PubSubClient (MQTT), ArduinoJson.
- **Persistent BLE connections** to both batteries (connect once, re-poll) → **~2 s live
  refresh** (vs ~30 s if reconnecting each cycle). Auto-reconnect on drop + **daily reboot**
  safety net.
- **Double-buffered display** (8-bit off-screen canvas; the 16-bit full-screen buffer didn't
  fit alongside WiFi+BLE) → **no flicker**; repaint only when a displayed value changes.
- **Parent panel:** big SOC ring (color by level) + big WATTS + `CHARGING`/`DISCHARGING`/`IDLE`
  + pack voltage + battery-health line. Whole-bank view (both batteries combined).
- **HA side:** MQTT auto-discovery publishes the full per-battery readout (SOC, V, I, P,
  capacities, cycles, temps, all 16 cells) — ~34 entities per battery.

## 7. Configuration / credentials

- CYD WiFi SSID: **`CHANGE_ME_IOT_SSID`**. Broker: HA Mosquitto at **`homeassistant.local:1883`**,
  user **`humsienk_cyd`** (see `cyd_monitor/MQTT_SETUP.md`).
- **Pending (HA side):** install + start the **Mosquitto broker add-on** and add the
  `humsienk_cyd` login — port 1883 was refusing connections (broker not running). The CYD
  auto-reconnects once it's up; no re-flash needed.

## 8. Open items & future work

- [ ] Stand up the **Mosquitto broker** on HA (blocks the HA data flow).
- [ ] **Verify the capacity divider** (`/10`?) against the app.
- [ ] **Charge/discharge FET state** in HA — decode the WATT `DP_WARNING_INFO` status registers
      (`handleWarningInfoResponse` in the decompiled `WattBleProtocolRepository`).
- [ ] Touchscreen **"Release BLE for 60 s"** button so the app can connect without unplugging.
- [ ] **ANENJI inverter integration** — read Modbus (USB now, WiFi later) for SOLAR / LOAD /
      TODAY kWh, replacing Solar Assistant's role. Add those to the display/HA.
- [ ] **Wired CAN/RS485 option** — the battery port is currently free; a transceiver
      (SN65HVD230 for CAN / MAX485 for RS485) would let the CYD read wired, sidestepping the
      BLE app-exclusion. Consider a passive sniffer once the inverter shares the bus.

## 9. Reverse-engineering journey (how we cracked it)

The scripts below were built during discovery. They're documented here as the record of
*how* the protocol was found, then removed to keep the repo lean — each is recreatable from
this description if ever needed.

**Phase 1 — Discovery (BLE)**
- `scan.py` — BLE scanner; first caught the batteries advertising and showed they're identified
  by name, not by the (bogus, identical) MAC the app displays.
- `enumerate.py` — dumped GATT services/characteristics (found `fff0`/`fff1`/`fff2`/`fffa`,
  `11110001…`, standard services).
- `probe.py` / `pounce.py` — robust "grab-on-sight" connectors: the batteries advertise only in
  brief bursts, so these scanned continuously and connected the instant a burst appeared.
- `watch.py` — logged advertisement RSSI/interval to gauge signal and find *both* batteries.
- `qr_decode.py` — OpenCV decode of the battery QR labels (proved QR = serial = BLE name).

**Phase 2/3 — Protocol dead-ends (the expensive red herring)**
- The "HS…" name matched the documented **BMC** protocol, so we chased that first:
  `humsienk.py` (clean BMC impl, validated offline against aiobmsble reference frames),
  `ref_*.py` (aiobmsble decoder/tests from PR #75), `read_once.py` / `bmc_read.py` (live BMC
  reads on `11110002/3` with `AA…` framing). **All silent.**
- `poll.py` — tried JBD / Daly / text commands. Silent.
- `find_channel.py` — brute-forced *every* TX/RX characteristic with the commands. Silent.
- `experiment.py` — tested BLE bonding (`pair()` → `AuthenticationFailed`) and serial-as-password.
- `capture.py` — passive notify capture; confirmed the BMS never streams unsolicited.

**The breakthrough — decompiling the app**
- Downloaded the Android APK; extracted/analysed it (`analyze_apk.py` via androguard,
  `decompile_targets.py` for smali), then used **jadx** for clean Java.
- Found `com.humsienk.hskpower` with one repository per protocol (Bmc/Jbd/Jk/**Watt**) and
  `BatteryDeviceManager`, which selects the protocol **by the device's GATT UUIDs** — not the
  name. Our `fff0/fff1/fff2/fffa` = the **WATT** device type, unlocked by writing **`"HiLink"`**
  to `fffa`. That single missing step was why everything before was silent.

**Phase 4/5 — the working solution (kept)**
- `watt.py` (protocol), `watt_read.py` / `watt_dual.py` (live reads), `mqtt_bridge.py` (HA
  bridge), and `cyd_monitor/` (the standalone firmware).

**Lesson:** these BMS advertise *which protocol they speak via their GATT UUIDs*. Matching the
"HS" name to the documented BMC protocol was the red herring that cost the most time — the app's
own `detectDeviceType()` (UUID-based) was the key insight.

## 10. Repo layout (post-cleanup)

- `cyd_monitor/` — **the CYD firmware** (PlatformIO). `src/watt.h` = protocol, `src/main.cpp`
  = BLE + display + MQTT. `MQTT_SETUP.md` = broker credential setup.
- `watt.py` — the protocol in Python (reference implementation).
- `watt_read.py` / `watt_dual.py` — laptop-side BLE readers (single / dual) — diagnostics
  and cross-checks.
- `mqtt_bridge.py` + `config.yaml` — laptop MQTT→HA bridge (an alternative/fallback to the
  CYD doing it; handy for testing).
- `reference/` — decompiled app protocol classes + aiobmsble reference (for the inverter/FET
  future work).
- `FINDINGS.md` — detailed technical log. `PROJECT_SCOPE.md` — this file.
