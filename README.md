# HumsiENK BLE Battery Monitor

**My 48 V batteries, read straight off Bluetooth — no vendor app, no cloud account, no Raspberry Pi.** A tiny ESP32 screen for the family and the full firehose of data in Home Assistant.

[![License: MIT](https://img.shields.io/github/license/arlak-lang/humsienk-ble-monitor)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/arlak-lang/humsienk-ble-monitor)](https://github.com/arlak-lang/humsienk-ble-monitor/commits/main)
[![Stars](https://img.shields.io/github/stars/arlak-lang/humsienk-ble-monitor?style=social)](https://github.com/arlak-lang/humsienk-ble-monitor/stargazers)

[![ESP32 / CYD](https://img.shields.io/badge/ESP32-Cheap_Yellow_Display-000000?logo=espressif&logoColor=white)](cyd_monitor/)
[![PlatformIO](https://img.shields.io/badge/PlatformIO-firmware-FF7F00?logo=platformio&logoColor=white)](cyd_monitor/platformio.ini)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-MQTT_discovery-41BDF5?logo=homeassistant&logoColor=white)](cyd_monitor/MQTT_SETUP.md)
[![Python](https://img.shields.io/badge/Python-tools-3776AB?logo=python&logoColor=white)](python/)

[![Cloud dependencies](https://img.shields.io/badge/cloud_dependencies-0-brightgreen)](#)
[![Apps required](https://img.shields.io/badge/apps_required-0-brightgreen)](#)
[![Made with](https://img.shields.io/badge/made_with-spite_%26_solder-red)](#)

```text
┌────────────────────────────────────────────────┐
│         ╭───────╮                 CHARGING ▲     │
│       ╭─┤       ├─╮                              │
│       │ │ 100 % │ │                 512          │
│       │ │       │ │                WATTS         │
│       ╰─┤       ├─╯                              │
│         ╰───────╯                                │
│   53.6 V                       2 batteries OK    │
└────────────────────────────────────────────────┘
       the whole point: a number, at a glance
```

---

## Why this exists

I bought a pair of perfectly good 48 V LiFePO₄ batteries. They have Bluetooth. Great — except the only way the manufacturer lets you *see* your own numbers is a phone app that wants an account, phones home, and shows one battery at a time if the stars align.

I have enough logins. I have enough apps. I did not want a Raspberry Pi and a subscription-flavored dashboard sitting between me and a **state-of-charge percentage**.

So I did what tired sysadmins do at 11pm: I sniffed the protocol, wrote it down, and cut the app out of the loop entirely. Now the batteries talk to a $12 screen and to Home Assistant, on my LAN, forever, for free. The vendor app can gather dust.

If you own these batteries and feel the same way about walled gardens, this is for you.

---

## What it does

- **Reads the batteries directly over BLE** — SOC, pack voltage, current, power, **every cell voltage**, temperatures, capacity, cycles. No app, no cloud, no middleman.
- **A dead-simple panel for humans** — the CYD ("Cheap Yellow Display", an ESP32 with a touchscreen) shows a big charge ring + watts + charging/discharging. Simple enough that the people who *use* the power don't need to understand any of it.
- **The full firehose for the nerd** — every reading is published to **Home Assistant** via MQTT auto-discovery (~34 sensors per battery). Graph it, alert on it, automate it.
- **Genuinely standalone** — the ESP32 does the Bluetooth, the screen, *and* the MQTT. Nothing else has to be powered on. No Pi. No Solar Assistant. No PC babysitting a dongle.
- **1, 2, or however many batteries** you have — nothing's hardcoded to a magic number.

---

## Hardware

| Thing | What / why |
|-------|------------|
| The batteries | HumsiENK / Shenzhen Shake World 48 V (51.2 V, 16S) LiFePO₄ with the HopeRF BLE module. Identified by BLE name = serial = the QR sticker. |
| The screen | **CYD** — ESP32-2432S028R, ~$10–15, 320×240 ILI9341 + touch. Has WiFi *and* BLE, which is the whole trick. |
| A computer (optional) | Only to flash the CYD and/or run the Python tools. After that, unplug it — the ESP32 is the whole system. |

---

## Quick start

**Just read a battery from a laptop** (sanity check before flashing anything):

```bash
cd python
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
python3 test_watt.py                                # unit tests, no hardware needed
./.venv/bin/python watt_read.py HS --name           # match your battery by name prefix
```

**Flash the standalone monitor:**

```bash
cd cyd_monitor
cp src/config.example.h src/config.h                # WiFi + MQTT (see MQTT_SETUP.md)
pio run -t upload                                    # PlatformIO; board = esp32dev
```

**Feed Home Assistant** — point it at your Mosquitto broker in `config.h`, create the login from
[`MQTT_SETUP.md`](cyd_monitor/MQTT_SETUP.md), and the batteries auto-appear as devices. Or run the
laptop bridge instead:

```bash
cd python && cp config.example.yaml config.yaml
./.venv/bin/python mqtt_bridge.py --dry-run --once   # prints what it would publish, no broker needed
```

### More than two batteries?

Nothing's fixed at two. Firmware: list them in the `BATTERIES[]` array in `config.h` (for 4+, bump
`CONFIG_BT_NIMBLE_MAX_CONNECTIONS` in `platformio.ini`). Bridge: list them under `batteries:` in
`config.yaml`. CLI: `watt_dual.py <name1> <name2> ...` takes as many as you throw at it.

---

## How it actually talks

The batteries speak a Tuya/Modbus-flavored dialect I've been calling **WATT/HiLink**. The one thing
that unlocks it: after connecting, write the ASCII bytes **`HiLink`** to characteristic `fffa`.
Then it's polite:

- Service `fff0`, notify `fff1`, write `fff2`, auth `fffa`
- Frames: `7E 00 01 03 <addr:u16> <count:u16> <crc16-modbus> 0D`
- Read data-point **140 (`0x8C`)** → cell count, cell voltages, temps, current, voltage, capacities, cycles, SOC

The whole map (and the two evenings of wrong turns it took to find it) is in
[`FINDINGS.md`](FINDINGS.md). The intent, architecture, and roadmap live in
[`PROJECT_SCOPE.md`](PROJECT_SCOPE.md).

---

## Tech stack

| Layer | What |
|-------|------|
| Firmware | ESP32 (Arduino / PlatformIO), NimBLE-Arduino (BLE central), LovyanGFX (display), PubSubClient + ArduinoJson (MQTT) |
| Protocol | Reverse-engineered WATT/HiLink over BLE GATT; Modbus-style framing, CRC-16/Modbus |
| Tools | Python 3 + `bleak` (BLE), `paho-mqtt` + `pyyaml` (the HA bridge) |
| Home | MQTT + Home Assistant auto-discovery. Broker of your choice — mine's the Mosquitto add-on. |

---

## Disclaimer / the boring-but-important part

Not affiliated with, endorsed by, or blessed by HumsiENK / Shenzhen Shake World. The protocol was
worked out from a battery **I own**, purely so it would interoperate with **my** house — which is
what reverse-engineering for interoperability is *for*. This repo ships **no** vendor app, firmware,
or decompiled code: just an independent description and a clean implementation.

It **only ever reads** — it never writes settings, toggles FETs, or otherwise pokes your BMS.
LiFePO₄ packs store a frightening amount of energy; you are the adult in the room. **No warranty.**
If the vendor has concerns, open an issue and I'll be reasonable.

---

## Credits

Built on the shoulders of people who also refuse to accept closed ecosystems:
[`aiobmsble`](https://github.com/patman15/aiobmsble) (the sibling *BMC* protocol — a red herring for
my units, but it lit the path), [NimBLE-Arduino](https://github.com/h2zero/NimBLE-Arduino),
[LovyanGFX](https://github.com/lovyan03/LovyanGFX), and the whole CYD tinkering community.

## License

[MIT](LICENSE) — take it, fork it, put it on your own battery, tell your friends. Copyright © 2026 arlak-lang.

## Author

**arlak-lang** — a tired IT guy who would rather spend a weekend reading someone else's BLE stack
than open one more phone app. [GitHub](https://github.com/arlak-lang)
