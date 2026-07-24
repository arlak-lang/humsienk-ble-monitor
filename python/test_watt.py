"""Unit tests for the WATT/HiLink protocol parser (watt.py).

Runs under pytest, or standalone: `python3 test_watt.py`.
The main vector is a REAL frame captured from a battery (full charge, 16S).
"""
import watt

# Real DP-140 reply captured from HS0000000000000001 (53.65 V, SOC 100%, 16 cells).
FRAME = bytes.fromhex(
    "7e000103008c003c"                                                  # header
    "10"                                                                # cellCount = 16
    "0d190d190d190d190d190d1a0d180d180d180d180d1a0d180d190d1b0d190d18"  # 16 cells
    "06" "0bae0bb80b9a0b9a0ba40ba4"                                     # 6 temps
    "0000" "14f5" "0064" "0064" "0003" "0064" "0064"                    # I,V,caps,cyc,soc
    "0493" "0d"                                                         # crc + tail
)


def test_crc16_modbus():
    assert watt.crc16_modbus(bytes.fromhex("7e000103008c0000")) == 0x9942


def test_build_read_frame():
    f = watt.build_read_frame(watt.DP_ANALOG_QUANTITY)
    assert f.hex() == "7e000103008c000099420d"
    assert f[0] == watt.HEAD_DEFAULT and f[-1] == watt.TAIL and len(f) == 11
    assert f[4:6] == bytes([0x00, 0x8C])          # address 140


def test_parse_frame():
    info = watt.parse_frame(FRAME)
    assert info is not None
    assert info["crc_ok"] is True
    assert info["start_addr"] == watt.DP_ANALOG_QUANTITY
    assert len(info["payload"]) == 60


def test_decode_analog():
    info = watt.parse_frame(FRAME)
    r = watt.decode_analog(info["payload"], info["new_version"])
    assert r["cell_count"] == 16
    assert r["soc"] == 100
    assert abs(r["voltage"] - 53.65) < 1e-6
    assert abs(r["current"] - 0.0) < 1e-6
    assert r["cycle_number"] == 3
    assert r["total_capacity"] == 100.0      # whole Ah (raw 100 = 100 Ah, not 10.0)
    assert r["design_capacity"] == 100.0
    assert r["remaining_capacity"] == 100.0
    assert abs(r["mos_temperature"] - 26.0) < 1e-6
    assert abs(r["pcb_temperature"] - 27.0) < 1e-6
    assert len(r["cell_voltages"]) == 16
    assert abs(min(r["cell_voltages"]) - 3.352) < 1e-6
    assert abs(max(r["cell_voltages"]) - 3.355) < 1e-6
    # sanity: sum of cells ≈ pack voltage (parallel/series-agnostic check for 16S)
    assert abs(sum(r["cell_voltages"]) - r["voltage"]) < 0.05


def test_current_sign_and_scale():
    # 14-bit magnitude; bit15 = sign(negative), bit14 = ÷10 flag.
    assert watt._parse_current(bytes([0x00, 0x64]), 0) == 100.0     # +100 A
    assert watt._parse_current(bytes([0x80, 0x64]), 0) == -100.0    # discharge
    assert watt._parse_current(bytes([0x40, 0x64]), 0) == 10.0      # ÷10 flag
    assert watt._parse_current(bytes([0xC0, 0x0A]), 0) == -1.0      # neg + ÷10


def test_extract_frames_handles_fragmentation():
    buf = bytearray(FRAME[:20])          # first fragment — incomplete
    assert watt.extract_frames(buf) == []
    buf += FRAME[20:]                     # rest arrives
    frames = watt.extract_frames(buf)
    assert len(frames) == 1 and frames[0] == FRAME
    assert buf == b""                     # fully consumed


def test_extract_frames_rejects_garbage():
    buf = bytearray(b"\x00\x11\x22" + FRAME)   # leading noise before SOF
    frames = watt.extract_frames(buf)
    assert len(frames) == 1 and frames[0] == FRAME


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
