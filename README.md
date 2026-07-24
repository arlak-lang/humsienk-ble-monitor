# HumsiENK BLE Battery Monitor

Read **HumsiENK / Shenzhen Shake World 48 V LiFePO₄ smart batteries** over Bluetooth LE —
without the vendor app — and surface them as a self-contained **ESP32 (CYD) display** and in
**Home Assistant** via MQTT auto-discovery.

The BLE protocol (a "WATT/HiLink" Tuya/Modbus-style dialect) was reverse-engineered for
interoperability; it's documented in [`FINDINGS.md`](FINDINGS.md) and
[`PROJECT_SCOPE.md`](PROJECT_SCOPE.md).

> ⚠️ **Disclaimer.** Not affiliated with, authorized, or endorsed by HumsiENK / Shenzhen Shake
> World. The protocol was reverse-engineered from a legally-owned device for personal
> interoperability; this repo contains **no** vendor app, firmware, or decompiled code — only
> an independent, clean-room description and implementation. This project **only reads** data
> (it never sends control/config writes). Working with high-capacity battery systems carries
> real risk. **No warranty; use at your own risk.**

## What it does

- Connects to each battery by its **BLE name (= serial = QR code)**, authenticates with the
  fixed `"HiLink"` key, and polls real-time data: **SOC, pack voltage, current, power, per-cell
  voltages, temperatures, capacity, cycles**.
- **CYD firmware** (`cyd_monitor/`) — an ESP32-2432S028R "Cheap Yellow Display" that reads both
  batteries directly, shows a simple EcoFlow-style panel (big SOC ring + watts + charge state),
  and publishes the full detail to Home Assistant. No Raspberry Pi / no always-on PC.
- **Python tools** (`python/`) — laptop-side readers and an MQTT→HA bridge (handy for testing
  or as an alternative to the firmware).

## Repo layout

```
cyd_monitor/            ESP32 (CYD) firmware — PlatformIO
  src/main.cpp          BLE + display + MQTT
  src/watt.h            the protocol
  src/config.example.h  copy to config.h and fill in WiFi/MQTT (config.h is gitignored)
  MQTT_SETUP.md         Home Assistant Mosquitto setup
python/
  watt.py               the protocol (reference implementation)
  watt_read.py          read one battery
  watt_dual.py          read both concurrently
  mqtt_bridge.py        publish to Home Assistant via MQTT discovery
  config.example.yaml   copy to config.yaml and edit
FINDINGS.md             detailed protocol notes + gotchas
PROJECT_SCOPE.md        project intent, architecture, roadmap
```

## Quick start — Python

```bash
cd python
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
python3 test_watt.py                                   # unit tests (no hardware needed)
# find/read a battery over BLE (needs a Bluetooth adapter):
./.venv/bin/python watt_read.py HS --name          # match by name prefix
./.venv/bin/python watt_dual.py <name1> <name2> ...    # read several at once
# publish to Home Assistant:
cp config.example.yaml config.yaml    # edit broker + battery names
./.venv/bin/python mqtt_bridge.py --dry-run --once     # test without a broker
```

The protocol parser (`watt.py`) is pure and covered by `test_watt.py` (run with
`python3 test_watt.py` or `pytest`), including a real captured frame.

## Number of batteries (1, 2, or N)

Everything scales to any count — nothing is hardcoded to two:
- **Firmware:** list your batteries in the `BATTERIES[]` array in `config.h`. For **4+**,
  also raise `CONFIG_BT_NIMBLE_MAX_CONNECTIONS` in `platformio.ini` to ≥ your count.
- **MQTT bridge:** list them under `batteries:` in `config.yaml`.
- **CLI readers:** `watt_read.py <name>` (one) or `watt_dual.py <name1> <name2> ...` (many).

## Quick start — CYD firmware

```bash
cd cyd_monitor
cp src/config.example.h src/config.h   # fill in WiFi + MQTT (see MQTT_SETUP.md)
pio run -t upload                       # PlatformIO; board = esp32dev / CYD
```

## Protocol in one paragraph

Transport: GATT service `fff0`, notify `fff1`, write `fff2`, **auth `fffa`**. Enable notify,
write ASCII **`HiLink`** to `fffa` to unlock, then exchange Modbus-style frames
(`7E 00 01 03 <addr:u16> <count:u16> <crc16-modbus> 0D`). Reading data-point **140 (`0x8C`)**
returns cell count + cell voltages, temps, current, voltage, capacities, cycles and SOC. Full
field map in [`FINDINGS.md`](FINDINGS.md).

## Credits

- [`aiobmsble`](https://github.com/patman15/aiobmsble) — reference for the sibling *BMC*
  protocol variant (which turned out not to be ours, but validated the approach).
- [NimBLE-Arduino](https://github.com/h2zero/NimBLE-Arduino),
  [LovyanGFX](https://github.com/lovyan03/LovyanGFX), and the CYD community.

## License

[MIT](LICENSE).
