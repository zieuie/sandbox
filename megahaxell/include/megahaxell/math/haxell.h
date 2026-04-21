#ifndef MEGAHAXELL_MATH_HAXELL_H
#define MEGAHAXELL_MATH_HAXELL_H

#include <stddef.h>
#include <stdint.h>

/*
  Port of multihaxell/haxell.py.

  Everything in this header is math-only: no networking, no ØMQ, no IO.
*/

struct mhx_haxell;

struct mhx_haxell *mhx_haxell_create(int n, int d, double eps);
void mhx_haxell_destroy(struct mhx_haxell *h);

/* Returns 1 if edge(a,b) is true under the multihaxell metric, else 0. */
int mhx_perm_edge(int n, int d, const uint16_t *a, const uint16_t *b);

/* A "vertex" is a permutation of length n. */
struct mhx_perm {
  int n;
  uint16_t *v; /* n entries */
};

struct mhx_perm mhx_perm_alloc(int n);
void mhx_perm_free(struct mhx_perm *p);
int mhx_perm_eq(const struct mhx_perm *a, const struct mhx_perm *b);

/*
  M is a partial transversal mapping colors -> vertices.
  This is intentionally a simple container to keep the math easy to edit.
*/
struct mhx_map_entry {
  uint8_t *color;      /* n bytes */
  struct mhx_perm perm; /* owns perm.v */
  size_t rank;         /* rank(color) in the color domain */
};

struct mhx_map {
  int n;
  size_t len;
  size_t cap;
  struct mhx_map_entry *e;

  int d;
  size_t domain;        /* total number of possible colors */
  size_t *pos_by_rank;  /* domain entries, SIZE_MAX means absent */
};

struct mhx_map mhx_map_create(int n, int d);
void mhx_map_destroy(struct mhx_map *m);
const struct mhx_perm *mhx_map_get(const struct mhx_map *m, const uint8_t *color);
int mhx_map_set(struct mhx_map *m, const uint8_t *color, const struct mhx_perm *perm);
int mhx_map_del(struct mhx_map *m, const uint8_t *color);

/*
  Attempt to grow the transversal so that color A is included.

  Returns:
    1 on success (diff is populated and M is updated),
    0 on failure (no change; diff is empty),
   -1 on error.
*/
int mhx_grow_transversal(struct mhx_haxell *h, struct mhx_map *M, const uint8_t *A, struct mhx_map *diff);

#endif /* MEGAHAXELL_MATH_HAXELL_H */
