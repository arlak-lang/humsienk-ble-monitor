#!/usr/bin/env python3
"""
ANENJI ANJ-12000W-LVP-WIFI inverter → MQTT / Home Assistant.  ⚠️ WIP.

Goal (same ethos as the battery side): read the inverter LOCALLY over its serial
port — solar, load, grid, battery flow — and publish to Home Assistant, so the
Energy dashboard has real numbers and nothing touches the vendor cloud.

CONFIRMED by probing (see `--probe`):
    • Protocol : Modbus RTU   (NOT Voltronic ASCII)
    • Serial   : 9600 baud, 8N1, on the inverter's RS485/USB cable (usually ttyUSB1)
    • Slave id : 1
    • Function : 3 (read holding registers)
    • Register 40000 (0x9C40) responds; a proper data block hasn't been mapped yet.

TODO — the register MAP. Which registers hold PV voltage/power, load, battery
voltage/current, SOC, grid, etc. is model-specific and still unknown. Options:
    1. `--scan A B` to walk the address space and log responders (patient; the
       device is silent on invalid regs, so use a short timeout).
    2. Find the published SmartESS / ANENJI Modbus map for this model.
Once mapped, fill in REGISTERS below and finish publish().

Deps:  pip install pyserial paho-mqtt
"""
import argparse
import struct
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    serial = None

# ── config (confirmed) ───────────────────────────────────────────────────────
SERIAL_PORT = "/dev/ttyUSB1"
BAUD = 9600
SLAVE = 1
MQTT_HOST = "homeassistant.local"
MQTT_PORT = 1883
MQTT_USER = "humsienk_cyd"
MQTT_PASSWORD = ""
MQTT_BASE = "anenji"
POLL_INTERVAL = 5

# ── register map — TO BE DISCOVERED. name: (address, scale, unit, device_class) ─
REGISTERS: dict[str, tuple[int, float, str, str]] = {
    # "pv_power":        (0x????, 1,    "W",  "power"),
    # "load_power":      (0x????, 1,    "W",  "power"),
    # "battery_voltage": (0x????, 0.1,  "V",  "voltage"),
    # "battery_soc":     (0x????, 1,    "%",  "battery"),
    # "grid_voltage":    (0x????, 0.1,  "V",  "voltage"),
}


# ── Modbus RTU ───────────────────────────────────────────────────────────────
def crc16_modbus(d: bytes) -> int:
    c = 0xFFFF
    for b in d:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


def read_registers(ser, addr: int, count: int = 1, slave: int = SLAVE):
    """Return list[int] of register values, or None on exception/timeout."""
    req = bytes([slave, 3]) + struct.pack(">HH", addr, count)
    ser.reset_input_buffer()
    ser.write(req + struct.pack("<H", crc16_modbus(req)))
    time.sleep(0.15 + count * 0.002)
    r = ser.read(5 + 2 * count + 4)
    if len(r) < 5 or r[1] != 3:          # 0x83 = exception, or no reply
        return None
    n = r[2] // 2
    return [int.from_bytes(r[3 + 2 * i:5 + 2 * i], "big") for i in range(n)]


def open_serial(port, baud):
    if serial is None:
        sys.exit("pyserial not installed — run: pip install pyserial")
    return serial.Serial(port, baud, timeout=0.6)


# ── probe / scan (how the protocol above was found) ──────────────────────────
def probe(port, baud):
    with open_serial(port, baud) as ser:
        print(f"Modbus probe {port} @ {baud}, slave {SLAVE}:")
        for a in (0, 1, 100, 0x9C40, 0x9C41):
            r = read_registers(ser, a)
            print(f"  reg {a} (0x{a:04x}) -> {r}")


def scan(port, baud, start, end):
    with open_serial(port, baud) as ser:
        ser.timeout = 0.25
        hits = 0
        for a in range(start, end):
            r = read_registers(ser, a)
            if r is not None:
                print(f"  {a} (0x{a:04x}) = {r[0]}")
                hits += 1
        print(f"{hits} responders in [{start}, {end})")


# ── normal mode (publish) — finish once REGISTERS is filled in ───────────────
def run():
    if not REGISTERS:
        sys.exit("REGISTERS is empty — map them first (see the TODO / --scan).")
    with open_serial(SERIAL_PORT, BAUD) as ser:
        while True:
            reading = {}
            for name, (addr, scale, _unit, _dc) in REGISTERS.items():
                v = read_registers(ser, addr)
                if v is not None:
                    reading[name] = round(v[0] * scale, 2)
            print(reading)   # TODO: publish to MQTT + HA discovery (mirror mqtt_bridge.py)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ANENJI inverter (Modbus RTU) → MQTT (WIP)")
    ap.add_argument("--probe", action="store_true", help="confirm Modbus responds")
    ap.add_argument("--scan", nargs=2, type=lambda x: int(x, 0), metavar=("START", "END"),
                    help="scan a register range for responders, e.g. --scan 0x9c40 0x9d00")
    ap.add_argument("--port", default=SERIAL_PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    args = ap.parse_args()
    if args.scan:
        scan(args.port, args.baud, args.scan[0], args.scan[1])
    elif args.probe:
        probe(args.port, args.baud)
    else:
        run()
