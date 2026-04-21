#ifndef MEGAHAXELL_MATH_COLORS_H
#define MEGAHAXELL_MATH_COLORS_H

#include <stddef.h>
#include <stdint.h>

/* A color is an array of length n, where each entry is a group id (0..groups-1). */
struct mhx_colors {
  int n;
  int groups;
  size_t count;
  uint8_t *data; /* count * n bytes */
};

/* Total number of colors used by make_colors(n,d) (multinomial count). */
int mhx_colors_count(int n, int d, size_t *out_count);

/* Rank a color (length n) in the same order as mhx_make_colors enumerates. */
int mhx_color_rank(int n, int d, const uint8_t *color, size_t *out_rank);

/* Generate all colorings used by multihaxell.make_colors(n, d). */
int mhx_make_colors(int n, int d, struct mhx_colors *out);
void mhx_colors_free(struct mhx_colors *c);

static inline const uint8_t *mhx_color_at(const struct mhx_colors *c, size_t i) {
  return c->data + (size_t)c->n * i;
}

#endif /* MEGAHAXELL_MATH_COLORS_H */
