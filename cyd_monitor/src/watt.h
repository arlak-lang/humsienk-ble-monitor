// WATT/HiLink BMS protocol — C++ port of the verified watt.py.
// Read frame: 7E 00 01 03 <addr:u16> <count:u16> <crc16modbus> 0D
// Reply     : 7E ver addr func <startAddr:u16> <len:u16> <payload> <crc16> 0D
#pragma once
#include <Arduino.h>

namespace watt {

static const uint8_t  HEAD_DEFAULT = 0x7E;
static const uint8_t  TAIL = 0x0D;
static const uint16_t DP_ANALOG_QUANTITY = 140;   // 0x8C — real-time V/I/SOC/temps/cells
static const char*    AUTH_KEY = "HiLink";        // written to the fffa auth char

inline uint16_t crc16_modbus(const uint8_t* d, size_t n) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    crc ^= d[i];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? ((crc >> 1) ^ 0xA001) : (crc >> 1);
  }
  return crc;
}

// Build an 11-byte read frame into out[]. Returns length (11).
inline size_t build_read_frame(uint16_t address, uint8_t* out, uint8_t head = HEAD_DEFAULT) {
  out[0] = head; out[1] = 0x00; out[2] = 0x01; out[3] = 0x03;
  out[4] = address >> 8; out[5] = address & 0xFF; out[6] = 0; out[7] = 0;
  uint16_t crc = crc16_modbus(out, 8);
  out[8] = crc >> 8; out[9] = crc & 0xFF; out[10] = TAIL;
  return 11;
}

struct Reading {
  bool  valid = false;
  int   cellCount = 0;
  float cells[32];
  int   tempCount = 0;
  float mosTemp = 0, pcbTemp = 0;
  int   cellTempCount = 0;
  float cellTemps[16];
  float current = 0, voltage = 0, power = 0;
  float remainingCapacity = 0, totalCapacity = 0, designCapacity = 0;
  int   cycleNumber = 0, soc = 0;
  float cellMin = 0, cellMax = 0, cellDelta = 0;
};

inline uint16_t u16(const uint8_t* b, int i) { return (uint16_t)((b[i] << 8) | b[i + 1]); }

inline float parse_current(const uint8_t* b, int i) {
  uint8_t hi = b[i], lo = b[i + 1];
  bool neg = hi & 0x80, scale10 = hi & 0x40;
  int mag = lo | ((hi & 0x3F) << 8);
  float d = scale10 ? mag / 10.0f : (float)mag;
  return neg ? -d : d;
}

// Parse a complete frame (must start 0x7E, end 0x0D). Fills r if it's an analog reply.
inline bool parse_analog_frame(const uint8_t* f, size_t len, Reading& r) {
  if (len < 11 || f[0] != HEAD_DEFAULT || f[len - 1] != TAIL) return false;
  uint16_t startAddr = u16(f, 4);
  uint16_t plen = u16(f, 6);
  if (startAddr != DP_ANALOG_QUANTITY) return false;
  if ((size_t)(8 + plen + 3) > len) return false;
  uint16_t crc_rx = (uint16_t)((f[len - 3] << 8) | f[len - 2]);
  if (crc16_modbus(f, len - 3) != crc_rx) return false;

  const uint8_t* p = f + 8;
  int i = 0;
  r.cellCount = p[i++];
  for (int c = 0; c < r.cellCount && c < 32; c++) { r.cells[c] = u16(p, i) / 1000.0f; i += 2; }
  r.tempCount = p[i++];
  r.mosTemp = (u16(p, i) - 2730) / 10.0f; i += 2;
  r.pcbTemp = (u16(p, i) - 2730) / 10.0f; i += 2;
  int ct = r.tempCount - 2; r.cellTempCount = ct > 0 ? ct : 0;
  for (int c = 0; c < r.cellTempCount && c < 16; c++) { r.cellTemps[c] = (u16(p, i) - 2730) / 10.0f; i += 2; }
  r.current = parse_current(p, i); i += 2;
  r.voltage = u16(p, i) / 100.0f; i += 2;
  r.remainingCapacity = u16(p, i) / 10.0f; i += 2;
  r.totalCapacity = u16(p, i) / 10.0f; i += 2;
  r.cycleNumber = u16(p, i); i += 2;
  r.designCapacity = u16(p, i) / 10.0f; i += 2;
  r.soc = u16(p, i); i += 2;
  r.power = r.voltage * r.current;
  if (r.cellCount > 0) {
    r.cellMin = r.cellMax = r.cells[0];
    for (int c = 1; c < r.cellCount; c++) {
      if (r.cells[c] < r.cellMin) r.cellMin = r.cells[c];
      if (r.cells[c] > r.cellMax) r.cellMax = r.cells[c];
    }
    r.cellDelta = r.cellMax - r.cellMin;
  }
  r.valid = true;
  return true;
}

}  // namespace watt
