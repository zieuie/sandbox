#include "megahaxell/math/colors.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

struct mhx_colors_builder {
  int n;
  size_t cap;
  size_t count;
  uint8_t *data;
};

static int mhx_builder_push(struct mhx_colors_builder *b, const uint8_t *color) {
  if (b->count == b->cap) {
    size_t new_cap = b->cap ? b->cap * 2 : 64;
    uint8_t *p = (uint8_t *)realloc(b->data, (size_t)b->n * new_cap);
    if (!p) return -1;
    b->data = p;
    b->cap = new_cap;
  }
  memcpy(b->data + (size_t)b->n * b->count, color, (size_t)b->n);
  b->count++;
  return 0;
}

static int mhx_recur(int i, int n, int groups, int *rem, uint8_t *sofar, struct mhx_colors_builder *b) {
  if (i >= n) {
    return mhx_builder_push(b, sofar);
  }
  for (int k = 0; k < groups; k++) {
    if (rem[k] <= 0) continue;
    rem[k]--;
    sofar[i] = (uint8_t)k;
    if (mhx_recur(i + 1, n, groups, rem, sofar, b) != 0) return -1;
    rem[k]++;
  }
  return 0;
}

int mhx_make_colors(int n, int d, struct mhx_colors *out) {
  if (!out || n <= 0 || d <= 0) return -1;

  int groups = (int)ceil((double)n / (double)d);
  int *rem = (int *)calloc((size_t)groups, sizeof(int));
  uint8_t *sofar = (uint8_t *)calloc((size_t)n, 1);
  if (!rem || !sofar) {
    free(rem);
    free(sofar);
    return -1;
  }
  for (int x = 0; x < groups; x++) {
    int hi = d * (x + 1);
    if (hi > n) hi = n;
    rem[x] = hi - d * x;
  }

  struct mhx_colors_builder b = {.n = n, .cap = 0, .count = 0, .data = NULL};
  int rc = mhx_recur(0, n, groups, rem, sofar, &b);
  free(rem);
  free(sofar);
  if (rc != 0) {
    free(b.data);
    return -1;
  }

  out->n = n;
  out->groups = groups;
  out->count = b.count;
  out->data = b.data;
  return 0;
}

void mhx_colors_free(struct mhx_colors *c) {
  if (!c) return;
  free(c->data);
  memset(c, 0, sizeof(*c));
}
