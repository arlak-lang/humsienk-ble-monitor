#!/usr/bin/env python3
"""
WATT / "HiLink" BMS protocol (Tuya/Modbus-style) as used by the HumsiENK app for
our batteries (device-type WATT: service fff0, write fff2, notify fff1, auth fffa).

Reverse-engineered from the decompiled app
(com.humsienk.hskpower.repository.WattBleProtocolRepository).

Transport / auth:
  - enable notify on fff1
  - write ASCII "HiLink" to the auth char fffa   (unlocks the BMS)
  - write read-frames to fff2; replies arrive as notify frames on fff1

Frame (big-endian, Modbus-flavoured):
  READ  : 7E 00 01 03 <addr:u16> <count:u16> <crc16:u16> 0D
  REPLY : 7E <ver> <addr> <func> <startAddr:u16> <len:u16> <payload[len]> <crc16:u16> 0D
  crc16 = Modbus CRC-16 (init 0xFFFF, poly 0xA001), transmitted big-endian.
  Head 0x7E (default) or 0x1E (alt); tail 0x0D.
"""
from __future__ import annotations

HEAD_DEFAULT = 0x7E
HEAD_ALT = 0x1E
TAIL = 0x0D
DP_ANALOG_QUANTITY = 140     # real-time V/I/SOC/temps/cells
AUTH_KEY = b"HiLink"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def build_read_frame(address: int, read_count: int = 0, head: int = HEAD_DEFAULT) -> bytes:
    body = bytes([head, 0x00, 0x01, 0x03]) + address.to_bytes(2, "big") + read_count.to_bytes(2, "big")
    crc = crc16_modbus(body)                      # over the 8-byte body
    return body + crc.to_bytes(2, "big") + bytes([TAIL])


def extract_frames(buf: bytearray) -> list[bytes]:
    """Pull complete WATT frames out of a rolling buffer (handles fragmentation)."""
    frames = []
    while True:
        # resync to a head byte
        while buf and buf[0] not in (HEAD_DEFAULT, HEAD_ALT):
            buf.pop(0)
        if len(buf) < 11:
            break
        payload_len = int.from_bytes(buf[6:8], "big")
        total = payload_len + 11
        if len(buf) < total:
            break
        frame = bytes(buf[:total])
        if frame[-1] == TAIL:
            frames.append(frame)
            del buf[:total]
        else:
            buf.pop(0)  # bad frame, resync
    return frames


def parse_frame(frame: bytes) -> dict | None:
    if len(frame) < 11 or frame[0] not in (HEAD_DEFAULT, HEAD_ALT) or frame[-1] != TAIL:
        return None
    ver, addr, func = frame[1], frame[2], frame[3]
    start_addr = int.from_bytes(frame[4:6], "big")
    length = int.from_bytes(frame[6:8], "big")
    payload = frame[8:8 + length]
    crc_rx = int.from_bytes(frame[-3:-1], "big")
    crc_calc = crc16_modbus(frame[:-3])
    return {
        "version": ver, "addr": addr, "func": func, "start_addr": start_addr,
        "payload": payload, "crc_ok": crc_rx == crc_calc, "new_version": ver >= 4,
    }


def _u16(b: bytes, i: int) -> int:
    return int.from_bytes(b[i:i + 2], "big")


def _parse_current(b: bytes, i: int) -> float:
    """14-bit magnitude; bit15 = sign(negative), bit14 = divide-by-10 flag."""
    hi, lo = b[i], b[i + 1]
    neg = bool(hi & 0x80)
    scale10 = bool(hi & 0x40)
    mag = lo | ((hi & 0x3F) << 8)
    d = mag / 10.0 if scale10 else float(mag)
    return -d if neg else d


def decode_analog(payload: bytes, new_version: bool = False) -> dict:
    """Decode a DP_ANALOG_QUANTITY (140) payload into a reading."""
    i = 0
    cell_count = payload[i]; i += 1
    cells = []
    for _ in range(cell_count):
        cells.append(_u16(payload, i) / 1000.0); i += 2
    temp_count = payload[i]; i += 1
    mos_temp = (_u16(payload, i) - 2730) / 10.0; i += 2
    pcb_temp = (_u16(payload, i) - 2730) / 10.0; i += 2
    cell_temps = []
    for _ in range(max(0, temp_count - 2)):
        cell_temps.append((_u16(payload, i) - 2730) / 10.0); i += 2
    current = _parse_current(payload, i); i += 2
    module_voltage = _u16(payload, i) / 100.0; i += 2
    remaining_capacity = _u16(payload, i) / 10.0; i += 2
    total_capacity = _u16(payload, i) / 10.0; i += 2
    cycle_number = _u16(payload, i); i += 2
    design_capacity = _u16(payload, i) / 10.0; i += 2
    soc = _u16(payload, i); i += 2
    out = {
        "cell_count": cell_count,
        "cell_voltages": cells,
        "temp_count": temp_count,
        "mos_temperature": mos_temp,
        "pcb_temperature": pcb_temp,
        "cell_temperatures": cell_temps,
        "current": current,
        "voltage": module_voltage,
        "remaining_capacity": remaining_capacity,
        "total_capacity": total_capacity,
        "cycle_number": cycle_number,
        "design_capacity": design_capacity,
        "soc": soc,
        "power": round(module_voltage * current, 2),
    }
    if cells:
        out["delta_voltage"] = round(max(cells) - min(cells), 3)
    return out


if __name__ == "__main__":
    # sanity: frame builds + CRC roundtrip
    f = build_read_frame(DP_ANALOG_QUANTITY)
    print("analog read frame:", f.hex())
    assert f[0] == 0x7E and f[-1] == 0x0D and len(f) == 11
    # round-trip a fake reply through crc + parser
    print("crc16([7E 00 01 03 00 8C 00 00]) =", hex(crc16_modbus(f[:8])))
    print("OK")
