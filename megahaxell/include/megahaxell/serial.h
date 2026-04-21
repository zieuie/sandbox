#ifndef MEGAHAXELL_SERIAL_H
#define MEGAHAXELL_SERIAL_H

#include <stddef.h>
#include <stdint.h>

/* Minimal serialization placeholder: encode a u64 in little-endian form. */
int mhx_serial_u64_le(uint64_t v, uint8_t out[8]);
int mhx_deserial_u64_le(const uint8_t in[8], uint64_t *out_v);

#endif /* MEGAHAXELL_SERIAL_H */
