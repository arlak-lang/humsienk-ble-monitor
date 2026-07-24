#!/usr/bin/env python3
"""
Live WATT/HiLink read: connect, auth with "HiLink" -> fffa, poll analog data.

Usage:
    ./.venv/bin/python watt_read.py HS0000000000 --name
"""
import argparse
import asyncio
import sys
import time
from bleak import BleakClient, BleakScanner
import watt

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
AUTH = "0000fffa-0000-1000-8000-00805f9b34fb"


async def grab(target, by_name, scan_timeout=90.0):
    t = target.upper()
    fut = asyncio.get_event_loop().create_future()

    def cb(device, adv):
        if not fut.done():
            name = (adv.local_name or device.name or "").upper()
            if (name.startswith(t) if by_name else device.address.upper() == t):
                print(f"  matched {device.address} name={adv.local_name} rssi={adv.rssi}")
                fut.set_result(device)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        return await asyncio.wait_for(fut, timeout=scan_timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await scanner.stop()


async def connect_with_retry(device, tries=3):
    for i in range(1, tries + 1):
        client = BleakClient(device)
        try:
            await client.connect()
            svcs = list(client.services)
            for _ in range(12):
                if svcs:
                    break
                await asyncio.sleep(1.0)
                svcs = list(client.services)
            if svcs:
                return client
            await client.disconnect()
            await asyncio.sleep(3.0)
        except Exception as e:
            print(f"  try {i}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(3.0)
    return None


async def main(target, by_name, logpath):
    device = await grab(target, by_name)
    if not device:
        print("Not seen advertising in 90s. Is the app closed?")
        return
    client = await connect_with_retry(device)
    if not client:
        print("Could not resolve GATT (try bluetoothctl power off/on).")
        return
    print(f"  connected to {device.name}")

    logf = open(logpath, "a")
    logf.write(f"# {device.address} {device.name} {time.strftime('%F %T')}\n")
    buf = bytearray()
    frames = []

    def on_notify(_c, data):
        b = bytes(data)
        logf.write(f"{time.monotonic():.3f} notif {b.hex()}\n"); logf.flush()
        buf.extend(b)
        for fr in watt.extract_frames(buf):
            frames.append(fr)
            print(f"    <== frame {fr.hex()}")

    await client.start_notify(NOTIFY, on_notify)
    print("  subscribed to fff1 (notify)")

    # AUTH: write "HiLink" to fffa (app uses WRITE_TYPE_DEFAULT = with response)
    for resp in (True, False):
        try:
            await client.write_gatt_char(AUTH, watt.AUTH_KEY, response=resp)
            print(f"  auth: wrote 'HiLink' to fffa (response={resp})")
            break
        except Exception as e:
            print(f"  auth write (response={resp}) failed: {e}")
    await asyncio.sleep(0.6)

    # Poll analog data; try both frame heads if needed.
    analog = None
    for head in (watt.HEAD_DEFAULT, watt.HEAD_ALT):
        rf = watt.build_read_frame(watt.DP_ANALOG_QUANTITY, head=head)
        print(f"\n  ==> analog read (head 0x{head:02x}): {rf.hex()}")
        before = len(frames)
        for resp in (False, True):
            try:
                await client.write_gatt_char(WRITE, rf, response=resp)
                break
            except Exception:
                continue
        # wait up to 3s for a reply
        end = time.monotonic() + 3.0
        while time.monotonic() < end and len(frames) == before:
            await asyncio.sleep(0.1)
        new = frames[before:]
        for fr in new:
            info = watt.parse_frame(fr)
            if info and info["start_addr"] == watt.DP_ANALOG_QUANTITY and info["payload"]:
                print(f"  analog reply: crc_ok={info['crc_ok']} ver={info['version']} "
                      f"len={len(info['payload'])}")
                analog = watt.decode_analog(info["payload"], info["new_version"])
                break
        if analog:
            break
        print("  (no analog reply for this head)")

    print("\n=== DECODED READING ===")
    if not analog:
        print("  No analog data decoded. Frames seen:", len(frames))
    else:
        v, c = analog["voltage"], analog["current"]
        print(f"  Pack voltage : {v:.2f} V")
        print(f"  Current      : {c:+.2f} A  ({'charging' if c > 0 else 'discharging'})")
        print(f"  Power        : {analog['power']:+.1f} W")
        print(f"  SOC          : {analog['soc']} %")
        print(f"  Capacity     : {analog['remaining_capacity']:.1f} / "
              f"{analog['total_capacity']:.1f} Ah (design {analog['design_capacity']:.1f})")
        print(f"  Cycles       : {analog['cycle_number']}")
        print(f"  MOS/PCB temp : {analog['mos_temperature']:.1f} / {analog['pcb_temperature']:.1f} °C")
        print(f"  Cell temps   : {analog['cell_temperatures']} °C")
        cv = analog["cell_voltages"]
        print(f"  Cells ({analog['cell_count']}): {[round(x,3) for x in cv]}")
        if cv:
            print(f"     min {min(cv):.3f}  max {max(cv):.3f}  Δ {max(cv)-min(cv):.3f} V")

    logf.close()
    try:
        await client.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--name", action="store_true")
    ap.add_argument("--log", default="watt_read.log")
    args = ap.parse_args()
    asyncio.run(main(args.target, args.name, args.log))
