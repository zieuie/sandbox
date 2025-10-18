#include "chebyshev.h"

bool pa_separated(const pa_t* pa, const cell_t d) {
  for (int u = 0; u < pa->m; u++) {
    for (int v = 0; v < u; v++) {
      if (!separated(pa, d, u, v)) {
        return false;
      }
    }
  }
  return true;
}
