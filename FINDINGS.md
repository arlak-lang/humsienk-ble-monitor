# HumsiENK / Shenzhen Shake World 48V BMS — reverse engineering notes

## Batteries (identifiers)
Model 48V100AH, 51.2V nominal (16S LiFePO4), 100Ah, 5120Wh.
Manufacturer: Shenzhen Shake World New Energy Technology Co., Ltd.
BLE module: **HopeRF** (Model `HP-BLE-1.0`, SW `HPZXGT01-C-V1.1`).

| Battery | BLE name = serial = QR | BlueZ address (public, stable) |
|---------|------------------------|--------------------------------|
| Left    | `HS0000000000000000`   | `AA:BB:CC:00:00:01`            |
| Right   | `HS0000000000000001`   | `AA:BB:CC:00:00:02`            |

- BLE name == printed serial == QR-code contents. **Identify batteries by BLE name.**
- Addresses are **public and stable** (not rotating). Same `AA:BB:CC:00:` block, seq last 2 bytes.
- The phone app shows both as `26:7B:88:A0:63:A1` — a firmware default, NOT a usable address. Ignore it.
- QR code = plain serial text only. No embedded key/password.

## Behaviour / connection gotchas
- Battery advertises **sparsely / in short bursts**, mostly right after a disconnect, then quiets.
  A phone app connected to a battery makes it stop advertising entirely (single connection slot).
- The iOS app connects even when "not visibly advertising" = it does a persistent direct
  connect by the known name/address and pounces on a burst. We mirror this in `pounce.py`.
- **CRITICAL BlueZ fix:** after failed connects/pairing, BlueZ gets into a state where it
  connects but resolves **0 GATT services** (MTU stuck at 23). Fix = clear state:
    bluetoothctl power off; sleep 3; bluetoothctl power on
    bluetoothctl remove <mac>   # clears cached (empty) GATT
  Also: **do NOT call pair()** — the device rejects bonding (`AuthenticationFailed`) and it's
  not needed. `Paired: no, Bonded: no` and GATT still resolves fine once cache is clean.
- Reconnect-on-empty retry (up to 3 fresh connects) added to pounce.py as belt-and-suspenders.

## GATT map (battery HS0000000000000001, confirmed working)
Reads below are mostly firmware DEFAULT/placeholder values, not live data.

- **180a Device Info**: 2a29="Hoperf", 2a28="HPZXGT01-C-V1.1", 2a24="HP-BLE-1.0",
  2a23=`123456fffe9abcde` (placeholder), 2a2a=`ffeeddccbbaa` (placeholder)
- **1800 Generic Access**: 2a00=name (read/WRITE), 2a01=`0000`, 2aa6=`01`
- **1801 Generic Attribute**: 2b29, 2b2a, 2a05(indicate)
- **Custom `11110001-1111-1111-1111-111111111111`**  <-- likely main data channel
  - `11110003-...` props: **write, notify**   (candidate: notify data stream)
  - `11110002-...` props: **write-without-response**  (candidate: command write)
- **fff0 serial service**  <-- alternate BMS-serial channel
  - `fff1` props: **read, notify**   (candidate notify)
  - `fff2` props: read, **write**-without-response, write   (candidate write)
  - `fffa` props: read, write-without-response, write

NOTE: our units expose `fff0` + custom `1111...`, NOT the JBD `ff00/ff01/ff02`.
(The sibling unit "HS30A3" in aiobmsble issue #65 advertised `ff00`+`0001` — different
firmware variant. Ref: https://github.com/patman15/aiobmsble/issues/65)

## Protocol (from aiobmsble PR #75 / issue #65 — validated offline, NOT yet live on our units)
Reference decoder: `ref_humsienk_bms.py`; our clean reimpl + passing self-test: `humsienk.py`.
- Framing: `AA <type> <len> <payload> <crc16_LE>`; crc = `sum(bytes[1:-2]) & 0xFFFF`.
- Command = `AA <cmd> 00 <cmd> 00`. Init `0x00` (required), data `0x20`(mosfets) `0x21`
  (V@3 u16/1000, I@7 s32/1000, SOC@11 u8, temps@23 6xs8) `0x22`(cells @3 u16/1000)
  `0x23`; info `0x11`(model) `0xF5`(hw). All little-endian. Self-test matches ref exactly.
- Reference TX/RX = chars `0002`/`0003` in service `0001`. Our variant's analog = `11110002`
  (write-no-resp) / `11110003` (notify) in service `11110001`.

## SOLVED — our units use the WATT/HiLink protocol, not BMC
Decompiled the HumsiENK Android app (`uni.UNI3890CA7` = `com.humsienk.hskpower`, uni-app-x /
Kotlin; protocol lib `com.gz.wattcycle`; FastBLE). `detectDeviceType` classifies by GATT UUIDs:
our `fff0`/`fff2`/`fff1`/`fffa` == device-type **WATT** (the "HS" name pointed at BMC, a red herring).
Decompiled source: `jadx_out/`; clean Python impl: `watt.py` + live reader `watt_read.py`.

- **Transport:** service `fff0`, notify `fff1`, write `fff2`, **auth `fffa`**.
- **THE missing step:** enable notify on `fff1`, then write ASCII **`"HiLink"`** to `fffa`.
  Without this the BMS ignores everything (that's why BMC/JBD/Daly attempts were silent).
  No user password for reads (getPassword/inputPassword are only for changing settings).
- **Framing (Modbus/Tuya, big-endian):**
  READ  `7E 00 01 03 <addr:u16> <count:u16> <crc16modbus> 0D`
  REPLY `7E ver addr func <startAddr:u16> <len:u16> <payload> <crc16> 0D`  (head 0x7E/alt 0x1E, tail 0x0D)
- **Real-time data:** read DP **140** (`0x8C`). Payload: cellCount u8; cells u16/1000 V;
  tempCount u8; mosTemp,pcbTemp (u16-2730)/10 C; cellTemps (u16-2730)/10; current (14-bit mag,
  bit15=sign,bit14=÷10); voltage u16/100 V; remain/total/design capacity u16 (whole Ah); cycles u16; SOC u16.

### First live read (right battery HS0000000000000001)
53.65 V, 0.00 A, SOC 100%, 16 cells ~3.353 V (Δ 0.003), MOS 26 / PCB 27 C, cell temps 24-25 C,
cycles 3, CRC valid. Cross-check: 16 x 3.353 = 53.65 V == reported pack voltage. ✅
- Capacity is whole Ah: raw 100 = 100 Ah (the vendor app's /10 giving 10.0 Ah is wrong for these packs). Fixed in the decoders.

## Remaining
- Phase 4 DONE: `watt_dual.py` held BOTH batteries on the single built-in adapter, 6/6 reads
  each, 0 drops, ~0.1s per concurrent poll cycle. No 2nd dongle needed.
- Phase 5 BUILT + dry-run validated: `mqtt_bridge.py` (+`config.yaml`). HA MQTT Discovery,
  35 entities/battery, concurrent dual poll, per-battery availability, BLE reconnect, scan-lock.
  Dry-run (`--dry-run --once`) connects both, prints all payloads. TO GO LIVE: fill broker
  host/port/user/pass in config.yaml (HA Mosquitto?), then run `./.venv/bin/python mqtt_bridge.py`.
- Follow-ups: (a) charge/discharge FET state — not in analog DP 140; needs decoding DP_WARNING_INFO
  status registers (handleWarningInfoResponse in decompiled WattBleProtocolRepository).
  (b) optional: systemd service for auto-start.
- Other DPs available in the app if wanted: warnings/protection params/FET control (30,50,70,140,146,...).
