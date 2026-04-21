#include "megahaxell/math/colors.h"

#include <stdio.h>

static int expect(int cond, const char *msg) {
  if (!cond) {
    fprintf(stderr, "FAIL: %s\n", msg);
    return 1;
  }
  return 0;
}

int main(void) {
  struct mhx_colors c;

  /* n=4,d=2 => groups=2 with counts 2/2 => multinomial = 6. */
  if (mhx_make_colors(4, 2, &c) != 0) return 1;
  int rc = 0;
  rc |= expect(c.n == 4, "n");
  rc |= expect(c.groups == 2, "groups");
  rc |= expect(c.count == 6, "count");
  mhx_colors_free(&c);

  /* n=3,d=2 => groups=2 with counts 2/1 => multinomial = 3. */
  if (mhx_make_colors(3, 2, &c) != 0) return 1;
  rc |= expect(c.groups == 2, "groups 3/2");
  rc |= expect(c.count == 3, "count 3/2");
  mhx_colors_free(&c);

  return rc;
}
