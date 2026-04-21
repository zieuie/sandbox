#include "megahaxell/core.h"

/* A small but non-trivial mixing function so the skeleton "does work". */
static uint64_t mhx_mix64(uint64_t x) {
  x += 0x9e3779b97f4a7c15ULL;
  x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
  x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
  return x ^ (x >> 31);
}

uint64_t mhx_core_compute(uint64_t x) {
  /* Placeholder: pretend this is expensive by iterating a few times. */
  for (int i = 0; i < 1000; i++) {
    x = mhx_mix64(x);
  }
  return x;
}
