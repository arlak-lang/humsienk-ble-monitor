#!/usr/bin/env python3
"""
Concurrent multi-battery read over one BLE adapter (works for 1, 2, or N).

Grabs each battery by BLE name, connects to all, authenticates with "HiLink",
then polls them concurrently for several cycles. Reports whether the single
adapter can sustain the connections (if not, you may need a 2nd BLE dongle).

Usage:
    ./.venv/bin/python watt_dual.py HS0000000000000000 HS0000000000000001
    ./.venv/bin/python watt_dual.py <name1> <name2> <name3> ... --cycles 6
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

NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
AUTH = "0000fffa-0000-1000-8000-00805f9b34fb"

async def grab_multiple(names: dict[str, str], scan_timeout: float):
    found = {}
    done = asyncio.Event()

    def cb(device, adv):
        nm = (adv.local_name or device.name or "").upper()
        for label, target in names.items():
            if nm == target.upper() and label not in found:
                found[label] = device
                print(f"  found {label} {target} @ {device.address} rssi={adv.rssi}")
        if len(found) == len(names):
            done.set()

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=scan_timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return found


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
            await asyncio.sleep(2.0)
        except Exception as e:
            print(f"    connect try {i}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(2.0)
    return None


class BatteryReader:
    def __init__(self, label, device):
        self.label = label
        self.device = device
        self.client = None
        self.buf = bytearray()
        self.frames = []
        self.evt = asyncio.Event()
        self.drops = 0

    def _on_notify(self, _c, data):
        self.buf.extend(bytes(data))
        for fr in watt.extract_frames(self.buf):
            self.frames.append(fr)
            self.evt.set()

    def _on_disconnect(self, _c):
        self.drops += 1
        print(f"    !! {self.label} disconnected")

    async def connect(self):
        self.client = await connect_with_retry(self.device)
        if not self.client:
            return False
        try:
            self.client.set_disconnected_callback(self._on_disconnect)
        except Exception:
            pass  # not available in bleak 3.x; drops still inferred from is_connected
        await self.client.start_notify(NOTIFY, self._on_notify)
        for resp in (True, False):
            try:
                await self.client.write_gatt_char(AUTH, watt.AUTH_KEY, response=resp)
                break
            except Exception:
                continue
        await asyncio.sleep(0.5)
        return True

    async def read_analog(self, timeout=3.0):
        if not self.client or not self.client.is_connected:
            return None
        before = len(self.frames)
        self.evt.clear()
        rf = watt.build_read_frame(watt.DP_ANALOG_QUANTITY)
        for resp in (False, True):
            try:
                await self.client.write_gatt_char(WRITE, rf, response=resp)
                break
            except Exception:
                return None
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.frames) == before:
            try:
                await asyncio.wait_for(self.evt.wait(), timeout=max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            self.evt.clear()
        for fr in self.frames[before:]:
            info = watt.parse_frame(fr)
            if info and info["start_addr"] == watt.DP_ANALOG_QUANTITY and info["payload"]:
                return watt.decode_analog(info["payload"], info["new_version"])
        return None

    async def disconnect(self):
        try:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
        except Exception:
            pass


async def main(targets, cycles, scan_timeout):
    print(f"Scanning for {len(targets)} batteries (up to {scan_timeout:.0f}s)...")
    found = await grab_multiple(targets, scan_timeout)
    if not found:
        print("Found no batteries advertising. Is the app closed?")
        return
    if len(found) < len(targets):
        print(f"WARNING: only found {list(found)} — proceeding with those.")

    readers = [BatteryReader(lbl, dev) for lbl, dev in found.items()]
    print("\nConnecting to both (sequentially)...")
    for r in readers:
        ok = await r.connect()
        print(f"  {r.label}: {'connected + authed' if ok else 'FAILED'}")
    live = [r for r in readers if r.client and r.client.is_connected]
    if len(live) < len(readers):
        print("  (not all connected)")

    print(f"\nPolling both concurrently for {cycles} cycles:")
    ok_counts = {r.label: 0 for r in readers}
    for c in range(1, cycles + 1):
        t0 = time.monotonic()
        results = await asyncio.gather(*[r.read_analog() for r in readers])
        dt = time.monotonic() - t0
        line = [f"cycle {c} ({dt:.1f}s)"]
        for r, res in zip(readers, results):
            if res:
                ok_counts[r.label] += 1
                line.append(f"{r.label}: {res['voltage']:.2f}V {res['current']:+.1f}A "
                            f"SOC {res['soc']}% up={r.client.is_connected}")
            else:
                line.append(f"{r.label}: -- (up={r.client.is_connected if r.client else False})")
        print("  " + " | ".join(line))
        await asyncio.sleep(1.5)

    print("\n=== VERDICT ===")
    for r in readers:
        print(f"  {r.label}: {ok_counts[r.label]}/{cycles} good reads, {r.drops} drop(s)")
    all_ok = bool(readers) and all(ok_counts[r.label] >= max(1, cycles // 2) for r in readers)
    if all_ok and len(found) == len(targets):
        print(f"  ✅ Single adapter sustained all {len(readers)} batteries concurrently.")
    elif len(found) < len(targets):
        print(f"  ⚠️  Only {len(found)}/{len(targets)} batteries were found — rerun.")
    else:
        print("  ⚠️  Concurrent polling was unreliable — a 2nd BLE dongle may help.")

    for r in readers:
        await r.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read N HumsiENK batteries concurrently.")
    ap.add_argument("names", nargs="+", help="BLE names (= serials) of the batteries to read")
    ap.add_argument("--cycles", type=int, default=6)
    ap.add_argument("--scan-timeout", type=float, default=150.0)
    args = ap.parse_args()
    targets = {f"BAT{i + 1}": n for i, n in enumerate(args.names)}
    asyncio.run(main(targets, args.cycles, args.scan_timeout))
