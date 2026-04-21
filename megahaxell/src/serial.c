#include "megahaxell/serial.h"

int mhx_serial_u64_le(uint64_t v, uint8_t out[8]) {
  if (!out) return -1;
  out[0] = (uint8_t)(v >> 0);
  out[1] = (uint8_t)(v >> 8);
  out[2] = (uint8_t)(v >> 16);
  out[3] = (uint8_t)(v >> 24);
  out[4] = (uint8_t)(v >> 32);
  out[5] = (uint8_t)(v >> 40);
  out[6] = (uint8_t)(v >> 48);
  out[7] = (uint8_t)(v >> 56);
  return 0;
}

int mhx_deserial_u64_le(const uint8_t in[8], uint64_t *out_v) {
  if (!in || !out_v) return -1;
  *out_v = 0;
  *out_v |= (uint64_t)in[0] << 0;
  *out_v |= (uint64_t)in[1] << 8;
  *out_v |= (uint64_t)in[2] << 16;
  *out_v |= (uint64_t)in[3] << 24;
  *out_v |= (uint64_t)in[4] << 32;
  *out_v |= (uint64_t)in[5] << 40;
  *out_v |= (uint64_t)in[6] << 48;
  *out_v |= (uint64_t)in[7] << 56;
  return 0;
}
