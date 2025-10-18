#ifndef CHEBYSHEV_H
#define CHEBYSHEV_H

#include "io.h"

inline bool separated(const pa_t* pa, const cell_t d, const int u, const int v) {
  for (int x = 0; x < pa->n; x++) {
    if ( abs( pa_get(pa, u, x) - pa_get(pa, v, x) ) >= d ) {
      return true;
    }
  }
  return false;
}

bool pa_separated(const pa_t* pa, const cell_t d);


#endif