#!/usr/bin/env python3
"""
Phase 5 - HumsiENK BMS -> MQTT bridge with Home Assistant MQTT Discovery.

Polls both batteries concurrently over BLE (WATT/HiLink protocol), publishes a
JSON state per battery, and auto-registers all entities in Home Assistant via
MQTT discovery. Includes BLE reconnect/retry and per-battery availability.

Run:
    ./.venv/bin/python mqtt_bridge.py                 # live (needs broker in config.yaml)
    ./.venv/bin/python mqtt_bridge.py --dry-run       # BLE only, print payloads, no MQTT
    ./.venv/bin/python mqtt_bridge.py --dry-run --once # one poll then exit
"""
import argparse
import asyncio
import json
import sys
import time

import yaml
from bleak import BleakClient, BleakScanner
import watt

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
AUTH = "0000fffa-0000-1000-8000-00805f9b34fb"

# Discovery sensor definitions: key -> (Name, unit, device_class, state_class, icon)
SENSORS = {
    "soc": ("SOC", "%", "battery", "measurement", None),
    "voltage": ("Voltage", "V", "voltage", "measurement", None),
    "current": ("Current", "A", "current", "measurement", None),
    "power": ("Power", "W", "power", "measurement", None),
    "remaining_capacity": ("Remaining Capacity", "Ah", None, "measurement", "mdi:battery-50"),
    "total_capacity": ("Total Capacity", "Ah", None, "measurement", "mdi:battery"),
    "design_capacity": ("Design Capacity", "Ah", None, "measurement", "mdi:battery-outline"),
    "cycle_number": ("Cycles", None, None, "total_increasing", "mdi:battery-sync"),
    "mos_temperature": ("MOSFET Temp", "°C", "temperature", "measurement", None),
    "pcb_temperature": ("PCB Temp", "°C", "temperature", "measurement", None),
    "cell_voltage_min": ("Cell Voltage Min", "V", "voltage", "measurement", None),
    "cell_voltage_max": ("Cell Voltage Max", "V", "voltage", "measurement", None),
    "cell_voltage_delta": ("Cell Voltage Delta", "V", "voltage", "measurement", "mdi:delta"),
    "cell_temp_min": ("Cell Temp Min", "°C", "temperature", "measurement", None),
    "cell_temp_max": ("Cell Temp Max", "°C", "temperature", "measurement", None),
}


def state_payload(a: dict) -> dict:
    """Flatten a decoded analog reading into an MQTT-friendly JSON dict."""
    cells = a.get("cell_voltages", [])
    temps = a.get("cell_temperatures", [])
    out = {
        "soc": a["soc"],
        "voltage": round(a["voltage"], 2),
        "current": round(a["current"], 2),
        "power": round(a["power"], 1),
        "remaining_capacity": round(a["remaining_capacity"], 1),
        "total_capacity": round(a["total_capacity"], 1),
        "design_capacity": round(a["design_capacity"], 1),
        "cycle_number": a["cycle_number"],
        "mos_temperature": round(a["mos_temperature"], 1),
        "pcb_temperature": round(a["pcb_temperature"], 1),
    }
    if cells:
        out["cell_voltage_min"] = round(min(cells), 3)
        out["cell_voltage_max"] = round(max(cells), 3)
        out["cell_voltage_delta"] = round(max(cells) - min(cells), 3)
        for i, v in enumerate(cells, 1):
            out[f"cell_{i}"] = round(v, 3)
    if temps:
        out["cell_temp_min"] = round(min(temps), 1)
        out["cell_temp_max"] = round(max(temps), 1)
        for i, t in enumerate(temps, 1):
            out[f"temp_{i}"] = round(t, 1)
    return out


def discovery_configs(serial: str, label: str, base: str, sample: dict):
    """Yield (config_topic, config_payload) for every entity of one battery."""
    device = {
        "identifiers": [f"humsienk_{serial}"],
        "name": f"HumsiENK {label} ({serial})",
        "manufacturer": "HumsiENK / Shenzhen Shake World",
        "model": "48V 100Ah LiFePO4 (WATT/HiLink)",
    }
    avail = f"{base}/{serial}/availability"
    state = f"{base}/{serial}/state"

    def cfg(key, name, unit, dclass, sclass, icon):
        uid = f"humsienk_{serial}_{key}"
        c = {
            "name": name,
            "unique_id": uid,
            "object_id": uid,
            "state_topic": state,
            "availability_topic": avail,
            "value_template": f"{{{{ value_json.{key} | default('unknown') }}}}",
            "device": device,
        }
        if unit:
            c["unit_of_measurement"] = unit
        if dclass:
            c["device_class"] = dclass
        if sclass:
            c["state_class"] = sclass
        if icon:
            c["icon"] = icon
        return (f"homeassistant/sensor/{uid}/config", c)

    for key, (name, unit, dclass, sclass, icon) in SENSORS.items():
        yield cfg(key, name, unit, dclass, sclass, icon)
    # per-cell voltages
    for k in sorted([k for k in sample if k.startswith("cell_") and k[5:].isdigit()],
                    key=lambda x: int(x[5:])):
        n = k[5:]
        yield cfg(k, f"Cell {n} Voltage", "V", "voltage", "measurement", None)
    # per-sensor temps
    for k in sorted([k for k in sample if k.startswith("temp_") and k[5:].isdigit()],
                    key=lambda x: int(x[5:])):
        n = k[5:]
        yield cfg(k, f"Cell Temp {n}", "°C", "temperature", "measurement", None)


# ---------------------------------------------------------------- BLE reader
# BlueZ allows only one active discovery at a time; serialize scans across readers.
_scan_lock = asyncio.Lock()


async def grab(name: str, scan_timeout: float):
    t = name.upper()
    async with _scan_lock:
        fut = asyncio.get_event_loop().create_future()

        def cb(device, adv):
            if not fut.done():
                nm = (adv.local_name or device.name or "").upper()
                if nm == t:
                    fut.set_result(device)

        scanner = BleakScanner(detection_callback=cb)
        await scanner.start()
        try:
            return await asyncio.wait_for(fut, timeout=scan_timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            await scanner.stop()


class BatteryReader:
    def __init__(self, label, name):
        self.label = label
        self.name = name          # BLE name == serial
        self.serial = name
        self.client = None
        self.buf = bytearray()
        self.frames = []
        self.evt = asyncio.Event()

    def _on_notify(self, _c, data):
        self.buf.extend(bytes(data))
        for fr in watt.extract_frames(self.buf):
            self.frames.append(fr)
            self.evt.set()

    @property
    def connected(self):
        return self.client is not None and self.client.is_connected

    async def ensure_connected(self) -> bool:
        if self.connected:
            return True
        self.client = None
        self.buf.clear()
        device = await grab(self.name, scan_timeout=30.0)
        if not device:
            return False
        client = BleakClient(device)
        try:
            await client.connect()
            for _ in range(12):
                if list(client.services):
                    break
                await asyncio.sleep(1.0)
            if not list(client.services):
                await client.disconnect()
                return False
            await client.start_notify(NOTIFY, self._on_notify)
            for resp in (True, False):
                try:
                    await client.write_gatt_char(AUTH, watt.AUTH_KEY, response=resp)
                    break
                except Exception:
                    continue
            await asyncio.sleep(0.5)
            self.client = client
            print(f"  [{self.label}] connected + authed ({device.address})")
            return True
        except Exception as e:
            print(f"  [{self.label}] connect failed: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return False

    async def read(self, timeout=3.0):
        if not await self.ensure_connected():
            return None
        before = len(self.frames)
        self.evt.clear()
        rf = watt.build_read_frame(watt.DP_ANALOG_QUANTITY)
        try:
            for resp in (False, True):
                try:
                    await self.client.write_gatt_char(WRITE, rf, response=resp)
                    break
                except Exception:
                    continue
            end = time.monotonic() + timeout
            while time.monotonic() < end and len(self.frames) == before:
                try:
                    await asyncio.wait_for(self.evt.wait(), timeout=max(0.05, end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                self.evt.clear()
            for fr in self.frames[before:]:
                info = watt.parse_frame(fr)
                if info and info["start_addr"] == watt.DP_ANALOG_QUANTITY and info["payload"] and info["crc_ok"]:
                    return watt.decode_analog(info["payload"], info["new_version"])
        except Exception as e:
            print(f"  [{self.label}] read error: {e}")
        return None

    async def disconnect(self):
        try:
            if self.connected:
                await self.client.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------- MQTT glue
class Mqtt:
    def __init__(self, cfg, dry_run):
        self.cfg = cfg
        self.dry = dry_run
        self.base = cfg["base_topic"]
        self.client = None
        self._discovered = set()

    def connect(self):
        if self.dry:
            print("[dry-run] MQTT disabled; payloads will be printed.")
            return
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client(client_id="humsienk-bridge")
        if self.cfg.get("username"):
            self.client.username_pw_set(self.cfg["username"], self.cfg.get("password", ""))
        self.client.will_set(f"{self.base}/bridge/availability", "offline", retain=True)
        # auto-reconnect with backoff (broker may be an ESP32 that reboots / comes and goes)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        def on_connect(_c, _u, _flags, rc, *a):
            print(f"[mqtt] connected (rc={rc}) to {self.cfg['host']}:{self.cfg.get('port',1883)}")
            self.pub(f"{self.base}/bridge/availability", "online", retain=True)
            # broker may have restarted and lost retained discovery -> republish next poll
            self._discovered.clear()

        def on_disconnect(_c, _u, *a):
            print("[mqtt] disconnected; will auto-reconnect...")

        self.client.on_connect = on_connect
        self.client.on_disconnect = on_disconnect
        # connect_async + loop_start keeps retrying even if the broker is down at startup
        self.client.connect_async(self.cfg["host"], int(self.cfg.get("port", 1883)), keepalive=60)
        self.client.loop_start()
        print(f"[mqtt] connecting to {self.cfg['host']}:{self.cfg.get('port',1883)} (async, will retry)")

    def pub(self, topic, payload, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        if self.dry:
            short = payload if len(str(payload)) < 120 else str(payload)[:117] + "..."
            print(f"[dry-run] PUB {topic} {'(retain) ' if retain else ''}{short}")
            return
        self.client.publish(topic, payload, retain=retain, qos=0)

    def ensure_discovery(self, reader: BatteryReader, sample: dict):
        if reader.serial in self._discovered:
            return
        n = 0
        for topic, cfg in discovery_configs(reader.serial, reader.label, self.base, sample):
            self.pub(topic, cfg, retain=True)
            n += 1
        self._discovered.add(reader.serial)
        print(f"  [{reader.label}] published {n} discovery configs")

    def publish_state(self, reader: BatteryReader, payload: dict):
        self.pub(f"{self.base}/{reader.serial}/availability", "online", retain=True)
        self.pub(f"{self.base}/{reader.serial}/state", payload, retain=False)

    def set_unavailable(self, reader: BatteryReader):
        self.pub(f"{self.base}/{reader.serial}/availability", "offline", retain=True)


async def run(cfg, dry_run, once):
    readers = [BatteryReader(b["label"], b["name"]) for b in cfg["batteries"]]
    mq = Mqtt(cfg["mqtt"], dry_run)
    mq.connect()
    interval = float(cfg.get("poll_interval", 10))
    try:
        while True:
            t0 = time.monotonic()
            results = await asyncio.gather(*[r.read() for r in readers])
            for r, a in zip(readers, results):
                if a is None:
                    print(f"  [{r.label}] no data (offline)")
                    mq.set_unavailable(r)
                    continue
                payload = state_payload(a)
                mq.ensure_discovery(r, payload)
                mq.publish_state(r, payload)
                print(f"  [{r.label}] {payload['voltage']}V {payload['current']:+}A "
                      f"SOC {payload['soc']}% | cells Δ{payload.get('cell_voltage_delta','?')}V")
            if once:
                break
            await asyncio.sleep(max(1.0, interval - (time.monotonic() - t0)))
    finally:
        for r in readers:
            await r.disconnect()
        if mq.client:
            mq.pub(f"{mq.base}/bridge/availability", "offline", retain=True)
            mq.client.loop_stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="no MQTT; print payloads")
    ap.add_argument("--once", action="store_true", help="single poll cycle then exit")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    asyncio.run(run(cfg, args.dry_run, args.once))


if __name__ == "__main__":
    main()
