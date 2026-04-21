#include "megahaxell/math/colors.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

static int mhx_fact_u64(int n, unsigned long long *out) {
  if (n < 0) return -1;
  unsigned long long v = 1;
  for (int i = 2; i <= n; i++) {
    if (v > (~0ULL) / (unsigned long long)i) return -1;
    v *= (unsigned long long)i;
  }
  *out = v;
  return 0;
}

static int mhx_multinomial_u64(int groups, const int *rem, int remaining, unsigned long long *out) {
  unsigned long long num;
  if (mhx_fact_u64(remaining, &num) != 0) return -1;

  __uint128_t den = 1;
  for (int g = 0; g < groups; g++) {
    unsigned long long f;
    if (mhx_fact_u64(rem[g], &f) != 0) return -1;
    den *= (__uint128_t)f;
  }
  if (den == 0) return -1;
  __uint128_t q = (__uint128_t)num / den;
  if (q > (__uint128_t)~0ULL) return -1;
  *out = (unsigned long long)q;
  return 0;
}

int mhx_colors_count(int n, int d, size_t *out_count) {
  if (!out_count || n <= 0 || d <= 0) return -1;
  int groups = (int)ceil((double)n / (double)d);
  int *rem = (int *)calloc((size_t)groups, sizeof(int));
  if (!rem) return -1;
  for (int x = 0; x < groups; x++) {
    int hi = d * (x + 1);
    if (hi > n) hi = n;
    rem[x] = hi - d * x;
  }

  unsigned long long total;
  int rc = mhx_multinomial_u64(groups, rem, n, &total);
  free(rem);
  if (rc != 0) return -1;
  *out_count = (size_t)total;
  return 0;
}

int mhx_color_rank(int n, int d, const uint8_t *color, size_t *out_rank) {
  if (!color || !out_rank || n <= 0 || d <= 0) return -1;

  int groups = (int)ceil((double)n / (double)d);
  int *rem = (int *)calloc((size_t)groups, sizeof(int));
  if (!rem) return -1;
  for (int x = 0; x < groups; x++) {
    int hi = d * (x + 1);
    if (hi > n) hi = n;
    rem[x] = hi - d * x;
  }

  unsigned long long rank = 0;
  for (int i = 0; i < n; i++) {
    int k = (int)color[i];
    if (k < 0 || k >= groups) {
      free(rem);
      return -1;
    }

    /* Count sequences with a smaller choice at this position. */
    for (int g = 0; g < k; g++) {
      if (rem[g] <= 0) continue;
      rem[g]--;
      unsigned long long add;
      if (mhx_multinomial_u64(groups, rem, n - i - 1, &add) != 0) {
        free(rem);
        return -1;
      }
      rank += add;
      rem[g]++;
    }

    if (rem[k] <= 0) {
      free(rem);
      return -1;
    }
    rem[k]--;
  }

  free(rem);
  *out_rank = (size_t)rank;
  return 0;
}

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
