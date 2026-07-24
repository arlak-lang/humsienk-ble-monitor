#!/usr/bin/env python3
"""
ANENJI ANJ-12000W-LVP-WIFI inverter → MQTT / Home Assistant.  ⚠️ SCAFFOLD / WIP.

Goal (same ethos as the battery side): read the inverter LOCALLY over its serial
port — solar production, load, grid, battery flow — and publish to Home Assistant,
so the Energy dashboard has real numbers and nothing touches the vendor cloud.

STATUS: the exact protocol isn't confirmed yet. These OEM 48 V hybrids are usually
**Voltronic** (ASCII `QPIGS` commands over RS232/USB, often 2400 baud) — that's what
this scaffolds. Some units are **Modbus RTU** instead. Run `--probe` FIRST to find out:

    ./.venv/bin/python anenji_bridge.py --probe --port /dev/ttyUSB1

If QPI/QPIGS return a sane "(...)" reply → it's Voltronic, fill in the field map below.
If they return silence/garbage → it's likely Modbus; see the TODO at the bottom.

Deps:  pip install pyserial paho-mqtt
"""
import argparse
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    serial = None

# ── config ───────────────────────────────────────────────────────────────────
SERIAL_PORT = "/dev/ttyUSB1"      # the inverter's USB cable (CYD is usually ttyUSB0)
BAUD = 2400                       # Voltronic default; some units use 9600 — probe both
MQTT_HOST = "homeassistant.local"
MQTT_PORT = 1883
MQTT_USER = "humsienk_cyd"        # reuse the same broker login, or make a new one
MQTT_PASSWORD = ""                # fill in
MQTT_BASE = "anenji"
POLL_INTERVAL = 5


# ── Voltronic framing (CRC-16/XMODEM with the usual byte fix-ups) ─────────────
def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def frame(cmd: str) -> bytes:
    body = cmd.encode()
    crc = crc16_xmodem(body)
    out = bytearray(body)
    for byte in (crc >> 8, crc & 0xFF):
        out.append(byte + 1 if byte in (0x28, 0x0D, 0x0A) else byte)  # Voltronic quirk
    out.append(0x0D)
    return bytes(out)


def query(ser, cmd: str, timeout: float = 2.0) -> bytes:
    ser.reset_input_buffer()
    ser.write(frame(cmd))
    end = time.time() + timeout
    buf = bytearray()
    while time.time() < end:
        chunk = ser.read(64)
        if chunk:
            buf += chunk
            if buf.endswith(b"\r"):
                break
    return bytes(buf)


# QPIGS field order (standard Voltronic). VERIFY against your unit's --probe output;
# split-phase 12 kW units often add fields / a second QPIGS2 for the 2nd phase.
QPIGS_FIELDS = [
    ("grid_voltage", "V"), ("grid_freq", "Hz"),
    ("ac_out_voltage", "V"), ("ac_out_freq", "Hz"),
    ("ac_out_va", "VA"), ("load_power", "W"), ("load_pct", "%"),
    ("bus_voltage", "V"), ("battery_voltage", "V"), ("battery_charge_current", "A"),
    ("battery_soc", "%"), ("inverter_temp", "°C"),
    ("pv_input_current", "A"), ("pv_input_voltage", "V"),
    ("scc_battery_voltage", "V"), ("battery_discharge_current", "A"),
    # ... status flags + newer fields (pv_power, etc.) follow — extend after probing
]


def parse_qpigs(resp: bytes) -> dict:
    s = resp.decode(errors="ignore").strip()
    if not s.startswith("("):
        return {}
    parts = s[1:].split(" ")
    out = {}
    for (name, _unit), val in zip(QPIGS_FIELDS, parts):
        try:
            out[name] = float(val)
        except ValueError:
            out[name] = val
    # solar power isn't always a direct field — derive if needed:
    if "pv_input_voltage" in out and "pv_input_current" in out:
        out["pv_power"] = round(out["pv_input_voltage"] * out["pv_input_current"], 1)
    return out


# ── probe mode: identify the protocol ─────────────────────────────────────────
def probe(port, baud):
    if serial is None:
        sys.exit("pyserial not installed — run: pip install pyserial")
    print(f"Probing {port} @ {baud} baud ...")
    with serial.Serial(port, baud, timeout=1) as ser:
        for cmd in ("QPI", "QID", "QMOD", "QPIGS", "QPIGS2", "QPGS0"):
            r = query(ser, cmd)
            print(f"  {cmd:8s} -> {r!r}")
    print("\nSane '(...)' replies = Voltronic (fill in QPIGS_FIELDS from QPIGS output).")
    print("Silence/garbage = probably Modbus RTU — see the TODO in this file.")


# ── normal mode: read + publish (publishing is a TODO stub) ───────────────────
def run():
    if serial is None:
        sys.exit("pyserial not installed — run: pip install pyserial")
    # TODO: connect MQTT (mirror mqtt_bridge.py: HA discovery for pv_power,
    #       load_power, battery_voltage, battery_soc, grid_voltage, etc.), then loop:
    with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as ser:
        while True:
            data = parse_qpigs(query(ser, "QPIGS"))
            print(data)  # TODO: publish to MQTT_BASE/state + HA discovery
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ANENJI inverter → MQTT (WIP scaffold)")
    ap.add_argument("--probe", action="store_true", help="identify the protocol and quit")
    ap.add_argument("--port", default=SERIAL_PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    args = ap.parse_args()
    if args.probe:
        probe(args.port, args.baud)
    else:
        run()

# ── TODO if it's Modbus RTU instead of Voltronic ──────────────────────────────
# - swap query()/frame() for minimalmodbus or pymodbus (RTU), find the slave id +
#   baud, and read the input registers for PV / load / battery / grid.
# - the register map is model-specific; capture it and add a parse_modbus().
