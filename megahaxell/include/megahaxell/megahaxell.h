#ifndef MEGAHAXELL_MEGAHAXELL_H
#define MEGAHAXELL_MEGAHAXELL_H

#include <stdint.h>

#define MHX_VERSION "0.1.0"

/* Library lifecycle (currently no-op, but keeps room for future init). */
int mhx_init(void);
void mhx_shutdown(void);

/* Placeholder "heavy compute" API. */
uint64_t mhx_core_compute(uint64_t x);

#endif /* MEGAHAXELL_MEGAHAXELL_H */
