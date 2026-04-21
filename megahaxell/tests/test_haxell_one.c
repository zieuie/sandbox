#include "megahaxell/math/colors.h"
#include "megahaxell/math/haxell.h"

#include <stdio.h>
#include <stdlib.h>

static int expect(int cond, const char *msg) {
  if (!cond) {
    fprintf(stderr, "FAIL: %s\n", msg);
    return 1;
  }
  return 0;
}

int main(void) {
  int rc = 0;
  const int n = 6;
  const int d = 3;
  const double eps = 0.1;

  struct mhx_colors colors;
  if (mhx_make_colors(n, d, &colors) != 0) return 1;

  struct mhx_haxell *h = mhx_haxell_create(n, d, eps);
  if (!h) {
    mhx_colors_free(&colors);
    return 1;
  }

  struct mhx_map M = mhx_map_create(n, d);
  struct mhx_map diff = mhx_map_create(n, d);

  const uint8_t *A = mhx_color_at(&colors, 0);
  int grow = mhx_grow_transversal(h, &M, A, &diff);

  rc |= expect(grow == 1, "grow should succeed for empty M");
  rc |= expect(M.len > 0, "M should have at least one entry");
  rc |= expect(diff.len > 0, "diff should have at least one entry");
  rc |= expect(mhx_map_get(&M, A) != NULL, "A should be in M after grow");
  rc |= expect(mhx_map_get(&diff, A) != NULL, "A should be in diff after grow");

  mhx_map_destroy(&diff);
  mhx_map_destroy(&M);
  mhx_haxell_destroy(h);
  mhx_colors_free(&colors);
  return rc;
}
