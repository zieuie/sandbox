#include "megahaxell/head.h"

#include <stdlib.h>

struct mhx_head {
  int reserved;
};

struct mhx_head *mhx_head_create(void) {
  struct mhx_head *h = (struct mhx_head *)calloc(1, sizeof(*h));
  return h;
}

void mhx_head_destroy(struct mhx_head *h) {
  free(h);
}
