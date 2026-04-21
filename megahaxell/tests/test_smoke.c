#include "megahaxell/core.h"
#include "megahaxell/serial.h"

#include <stdint.h>
#include <stdio.h>

int main(void) {
  uint64_t x = 123;
  uint64_t y = mhx_core_compute(x);
  if (y == 0) {
    fprintf(stderr, "unexpected compute result\n");
    return 1;
  }

  uint8_t buf[8];
  if (mhx_serial_u64_le(y, buf) != 0) return 1;
  uint64_t z = 0;
  if (mhx_deserial_u64_le(buf, &z) != 0) return 1;
  if (z != y) {
    fprintf(stderr, "serial roundtrip mismatch\n");
    return 1;
  }

  return 0;
}
