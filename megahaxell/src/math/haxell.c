#include "megahaxell/math/haxell.h"

#include <stdint.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

struct mhx_haxell {
  int n;
  int d;
  int groups;
  uint64_t dsq;
  double mu;
  int U;
  double rho;

  /* ident_class in the Python: rows_count * n entries, row-major */
  size_t ident_rows_count;
  uint16_t *ident_rows;
};

static int mhx_color_eq(const uint8_t *a, const uint8_t *b, int n) {
  return memcmp(a, b, (size_t)n) == 0;
}

static int mhx_edge(const struct mhx_haxell *h, const struct mhx_perm *a, const struct mhx_perm *b) {
  for (int i = 0; i < h->n; i++) {
    int da = (int)a->v[i] - (int)b->v[i];
    uint64_t dsq = (uint64_t)(da * da);
    if (dsq >= h->dsq) return 0;
  }
  return 1;
}

int mhx_perm_edge(int n, int d, const uint16_t *a, const uint16_t *b) {
  if (!a || !b || n <= 0 || d <= 0) return 0;
  uint64_t dsq = (uint64_t)d * (uint64_t)d;
  for (int i = 0; i < n; i++) {
    int da = (int)a[i] - (int)b[i];
    uint64_t x = (uint64_t)(da * da);
    if (x >= dsq) return 0;
  }
  return 1;
}

static uint8_t *mhx_color_dup(const uint8_t *c, int n) {
  uint8_t *p = (uint8_t *)malloc((size_t)n);
  if (!p) return NULL;
  memcpy(p, c, (size_t)n);
  return p;
}

struct mhx_perm mhx_perm_alloc(int n) {
  struct mhx_perm p;
  p.n = n;
  p.v = (uint16_t *)calloc((size_t)n, sizeof(uint16_t));
  return p;
}

void mhx_perm_free(struct mhx_perm *p) {
  if (!p) return;
  free(p->v);
  p->v = NULL;
  p->n = 0;
}

int mhx_perm_eq(const struct mhx_perm *a, const struct mhx_perm *b) {
  if (!a || !b || a->n != b->n) return 0;
  return memcmp(a->v, b->v, (size_t)a->n * sizeof(uint16_t)) == 0;
}

struct mhx_map mhx_map_create(int n) {
  struct mhx_map m;
  m.n = n;
  m.len = 0;
  m.cap = 0;
  m.e = NULL;
  return m;
}

static void mhx_entry_free(struct mhx_map_entry *e) {
  if (!e) return;
  free(e->color);
  mhx_perm_free(&e->perm);
  memset(e, 0, sizeof(*e));
}

void mhx_map_destroy(struct mhx_map *m) {
  if (!m) return;
  for (size_t i = 0; i < m->len; i++) {
    mhx_entry_free(&m->e[i]);
  }
  free(m->e);
  memset(m, 0, sizeof(*m));
}

const struct mhx_perm *mhx_map_get(const struct mhx_map *m, const uint8_t *color) {
  if (!m || !color) return NULL;
  for (size_t i = 0; i < m->len; i++) {
    if (mhx_color_eq(m->e[i].color, color, m->n)) return &m->e[i].perm;
  }
  return NULL;
}

int mhx_map_del(struct mhx_map *m, const uint8_t *color) {
  if (!m || !color) return -1;
  for (size_t i = 0; i < m->len; i++) {
    if (mhx_color_eq(m->e[i].color, color, m->n)) {
      mhx_entry_free(&m->e[i]);
      /* swap-delete */
      if (i != m->len - 1) m->e[i] = m->e[m->len - 1];
      m->len--;
      return 0;
    }
  }
  return 0;
}

int mhx_map_set(struct mhx_map *m, const uint8_t *color, const struct mhx_perm *perm) {
  if (!m || !color || !perm || perm->n != m->n) return -1;

  for (size_t i = 0; i < m->len; i++) {
    if (mhx_color_eq(m->e[i].color, color, m->n)) {
      /* replace */
      memcpy(m->e[i].perm.v, perm->v, (size_t)m->n * sizeof(uint16_t));
      return 0;
    }
  }

  if (m->len == m->cap) {
    size_t new_cap = m->cap ? m->cap * 2 : 64;
    struct mhx_map_entry *p = (struct mhx_map_entry *)realloc(m->e, new_cap * sizeof(*m->e));
    if (!p) return -1;
    m->e = p;
    m->cap = new_cap;
  }

  struct mhx_map_entry *e = &m->e[m->len++];
  memset(e, 0, sizeof(*e));
  e->color = mhx_color_dup(color, m->n);
  if (!e->color) return -1;
  e->perm = mhx_perm_alloc(m->n);
  if (!e->perm.v) return -1;
  memcpy(e->perm.v, perm->v, (size_t)m->n * sizeof(uint16_t));
  return 0;
}

static int mhx_feasible_constants(int r, double eps, double *mu, int *U, double *rho) {
  if (r < 2) return -1;
  if (!(eps > 0.0 && eps < 1.0)) return -1;
  *mu = eps / (10.0 * (double)r);
  double U_real = (10.0 * (double)r) / eps;
  *U = (int)ceil(U_real);
  *rho = eps / (10.0 * (double)r);
  return 0;
}

/* Hash-set of color vectors (n bytes) to count unique values. */
struct mhx_color_set {
  int n;
  size_t cap;  /* power of 2 */
  size_t len;
  uint8_t *used;
  uint8_t *keys; /* cap * n bytes */
};

static uint64_t mhx_fnv1a64(const uint8_t *p, int n) {
  uint64_t h = 1469598103934665603ULL;
  for (int i = 0; i < n; i++) {
    h ^= (uint64_t)p[i];
    h *= 1099511628211ULL;
  }
  return h;
}

static int mhx_color_set_init(struct mhx_color_set *s, int n, size_t cap) {
  memset(s, 0, sizeof(*s));
  s->n = n;
  /* round cap to power-of-two >= 16 */
  size_t c = 16;
  while (c < cap) c <<= 1;
  s->cap = c;
  s->used = (uint8_t *)calloc(s->cap, 1);
  s->keys = (uint8_t *)calloc(s->cap * (size_t)n, 1);
  if (!s->used || !s->keys) return -1;
  return 0;
}

static void mhx_color_set_free(struct mhx_color_set *s) {
  if (!s) return;
  free(s->used);
  free(s->keys);
  memset(s, 0, sizeof(*s));
}

static int mhx_color_set_rehash(struct mhx_color_set *s, size_t new_cap) {
  struct mhx_color_set nset;
  if (mhx_color_set_init(&nset, s->n, new_cap) != 0) return -1;

  for (size_t i = 0; i < s->cap; i++) {
    if (!s->used[i]) continue;
    const uint8_t *k = s->keys + i * (size_t)s->n;
    uint64_t h = mhx_fnv1a64(k, s->n);
    size_t mask = nset.cap - 1;
    size_t idx = (size_t)h & mask;
    while (nset.used[idx]) idx = (idx + 1) & mask;
    nset.used[idx] = 1;
    memcpy(nset.keys + idx * (size_t)s->n, k, (size_t)s->n);
    nset.len++;
  }

  mhx_color_set_free(s);
  *s = nset;
  return 0;
}

static int mhx_color_set_insert(struct mhx_color_set *s, const uint8_t *k) {
  if (s->len * 10 >= s->cap * 7) {
    if (mhx_color_set_rehash(s, s->cap * 2) != 0) return -1;
  }
  uint64_t h = mhx_fnv1a64(k, s->n);
  size_t mask = s->cap - 1;
  size_t idx = (size_t)h & mask;
  for (;;) {
    if (!s->used[idx]) {
      s->used[idx] = 1;
      memcpy(s->keys + idx * (size_t)s->n, k, (size_t)s->n);
      s->len++;
      return 1;
    }
    uint8_t *slot = s->keys + idx * (size_t)s->n;
    if (memcmp(slot, k, (size_t)s->n) == 0) return 0;
    idx = (idx + 1) & mask;
  }
}

struct mhx_r_ctx {
  int n;
  int d;
  uint8_t *qt; /* n bytes */
  uint8_t *pt; /* n bytes */
  uint8_t *used; /* n bytes */
  uint16_t *sofar; /* n u16 */
  struct mhx_color_set set;
};

static int mhx_edge_to_ident(int n, int d, const uint16_t *p) {
  for (int i = 0; i < n; i++) {
    int da = (int)p[i] - i;
    if (da < 0) da = -da;
    if (da >= d) return 0;
  }
  return 1;
}

static int mhx_recur_r(struct mhx_r_ctx *c, int i) {
  if (i >= c->n) {
    /* compute pt = color(p) */
    for (int k = 0; k < c->n; k++) c->pt[k] = (uint8_t)(c->sofar[k] / (uint16_t)c->d);
    if (memcmp(c->pt, c->qt, (size_t)c->n) == 0) return 0;
    if (!mhx_edge_to_ident(c->n, c->d, c->sofar)) return 0;
    int ins = mhx_color_set_insert(&c->set, c->pt);
    return (ins < 0) ? -1 : 0;
  }

  int lo = i - (c->d - 1);
  int hi = i + (c->d - 1);
  if (lo < 0) lo = 0;
  if (hi >= c->n) hi = c->n - 1;

  for (int e = lo; e <= hi; e++) {
    if (c->used[e]) continue;
    c->used[e] = 1;
    c->sofar[i] = (uint16_t)e;
    if (mhx_recur_r(c, i + 1) != 0) return -1;
    c->used[e] = 0;
  }
  return 0;
}

static int mhx_compute_r(int n, int d, int *out_r) {
  if (!out_r) return -1;
  *out_r = 0;

  struct mhx_r_ctx c;
  memset(&c, 0, sizeof(c));
  c.n = n;
  c.d = d;
  c.qt = (uint8_t *)malloc((size_t)n);
  c.pt = (uint8_t *)malloc((size_t)n);
  c.used = (uint8_t *)calloc((size_t)n, 1);
  c.sofar = (uint16_t *)calloc((size_t)n, sizeof(uint16_t));
  if (!c.qt || !c.pt || !c.used || !c.sofar) goto fail;
  for (int i = 0; i < n; i++) c.qt[i] = (uint8_t)(i / d);

  if (mhx_color_set_init(&c.set, n, 128) != 0) goto fail;
  if (mhx_recur_r(&c, 0) != 0) goto fail;

  *out_r = (int)c.set.len;
  mhx_color_set_free(&c.set);
  free(c.qt);
  free(c.pt);
  free(c.used);
  free(c.sofar);
  return 0;

fail:
  mhx_color_set_free(&c.set);
  free(c.qt);
  free(c.pt);
  free(c.used);
  free(c.sofar);
  return -1;
}

static int mhx_factorial(int k, size_t *out) {
  if (k < 0) return -1;
  size_t v = 1;
  for (int i = 2; i <= k; i++) {
    if (v > (SIZE_MAX / (size_t)i)) return -1;
    v *= (size_t)i;
  }
  *out = v;
  return 0;
}

struct mhx_perm_list {
  int n;
  size_t len;
  size_t cap;
  struct mhx_perm *items;
};

static void mhx_perm_list_free(struct mhx_perm_list *l) {
  if (!l) return;
  for (size_t i = 0; i < l->len; i++) {
    mhx_perm_free(&l->items[i]);
  }
  free(l->items);
  memset(l, 0, sizeof(*l));
}

static int mhx_perm_list_push(struct mhx_perm_list *l, const uint16_t *v) {
  if (l->len == l->cap) {
    size_t new_cap = l->cap ? l->cap * 2 : 16;
    struct mhx_perm *p = (struct mhx_perm *)realloc(l->items, new_cap * sizeof(*l->items));
    if (!p) return -1;
    l->items = p;
    l->cap = new_cap;
  }
  struct mhx_perm *dst = &l->items[l->len++];
  *dst = mhx_perm_alloc(l->n);
  if (!dst->v) return -1;
  memcpy(dst->v, v, (size_t)l->n * sizeof(uint16_t));
  return 0;
}

static int mhx_perm_list_contains(const struct mhx_perm_list *l, const uint16_t *v) {
  for (size_t i = 0; i < l->len; i++) {
    if (memcmp(l->items[i].v, v, (size_t)l->n * sizeof(uint16_t)) == 0) return 1;
  }
  return 0;
}

struct mhx_xentry {
  uint8_t *color; /* n bytes */
  struct mhx_perm_list set;
};

struct mhx_xmap {
  int n;
  size_t len;
  size_t cap;
  struct mhx_xentry *e;
};

static struct mhx_xmap mhx_xmap_create(int n) {
  struct mhx_xmap m;
  m.n = n;
  m.len = 0;
  m.cap = 0;
  m.e = NULL;
  return m;
}

static void mhx_xmap_destroy(struct mhx_xmap *m) {
  if (!m) return;
  for (size_t i = 0; i < m->len; i++) {
    free(m->e[i].color);
    mhx_perm_list_free(&m->e[i].set);
  }
  free(m->e);
  memset(m, 0, sizeof(*m));
}

static struct mhx_perm_list *mhx_xmap_get(struct mhx_xmap *m, const uint8_t *color) {
  for (size_t i = 0; i < m->len; i++) {
    if (mhx_color_eq(m->e[i].color, color, m->n)) return &m->e[i].set;
  }
  return NULL;
}

static const struct mhx_perm_list *mhx_xmap_get_const(const struct mhx_xmap *m, const uint8_t *color) {
  for (size_t i = 0; i < m->len; i++) {
    if (mhx_color_eq(m->e[i].color, color, m->n)) return &m->e[i].set;
  }
  return NULL;
}

static struct mhx_perm_list *mhx_xmap_ensure(struct mhx_xmap *m, const uint8_t *color) {
  struct mhx_perm_list *s = mhx_xmap_get(m, color);
  if (s) return s;

  if (m->len == m->cap) {
    size_t new_cap = m->cap ? m->cap * 2 : 32;
    struct mhx_xentry *p = (struct mhx_xentry *)realloc(m->e, new_cap * sizeof(*m->e));
    if (!p) return NULL;
    m->e = p;
    m->cap = new_cap;
  }
  struct mhx_xentry *e = &m->e[m->len++];
  memset(e, 0, sizeof(*e));
  e->color = mhx_color_dup(color, m->n);
  if (!e->color) return NULL;
  e->set.n = m->n;
  return &e->set;
}

static int mhx_xmap_add_perm(struct mhx_xmap *m, const uint8_t *color, const uint16_t *perm) {
  struct mhx_perm_list *s = mhx_xmap_ensure(m, color);
  if (!s) return -1;
  if (mhx_perm_list_contains(s, perm)) return 0;
  return mhx_perm_list_push(s, perm);
}

static size_t mhx_xmap_total_len(const struct mhx_xmap *m) {
  size_t total = 0;
  for (size_t i = 0; i < m->len; i++) total += m->e[i].set.len;
  return total;
}

static int mhx_xmap_clone_into(const struct mhx_xmap *src, struct mhx_xmap *dst) {
  *dst = mhx_xmap_create(src->n);
  for (size_t i = 0; i < src->len; i++) {
    const struct mhx_xentry *e = &src->e[i];
    struct mhx_perm_list *s = mhx_xmap_ensure(dst, e->color);
    if (!s) return -1;
    for (size_t j = 0; j < e->set.len; j++) {
      if (mhx_perm_list_push(s, e->set.items[j].v) != 0) return -1;
    }
  }
  return 0;
}

static int mhx_map_clone_into(const struct mhx_map *src, struct mhx_map *dst) {
  *dst = mhx_map_create(src->n);
  for (size_t i = 0; i < src->len; i++) {
    if (mhx_map_set(dst, src->e[i].color, &src->e[i].perm) != 0) return -1;
  }
  return 0;
}

static int mhx_blocks(const struct mhx_haxell *h, const struct mhx_map *M, const uint8_t *av, const struct mhx_perm *v) {
  for (size_t i = 0; i < M->len; i++) {
    const uint8_t *au = M->e[i].color;
    if (mhx_color_eq(au, av, h->n)) continue;
    if (mhx_edge(h, &M->e[i].perm, v)) return 1;
  }
  return 0;
}

static int mhx_immediately_addable(const struct mhx_haxell *h, const struct mhx_map *M, const struct mhx_xmap *W, struct mhx_xmap *out_I) {
  *out_I = mhx_xmap_create(W->n);
  for (size_t i = 0; i < W->len; i++) {
    const struct mhx_xentry *e = &W->e[i];
    for (size_t j = 0; j < e->set.len; j++) {
      const struct mhx_perm *v = &e->set.items[j];
      if (!mhx_blocks(h, M, e->color, v)) {
        if (mhx_xmap_add_perm(out_I, e->color, v->v) != 0) return -1;
      }
    }
  }
  return 0;
}

static int mhx_is_addable(
    const struct mhx_haxell *h,
    const struct mhx_map *M,
    const struct mhx_xmap *X_layers,
    const struct mhx_map *Y_layers,
    size_t layers,
    const struct mhx_xmap *x,
    const struct mhx_map *y,
    const uint8_t *a,
    const uint16_t *v) {
  (void)M;
  /* v not in Y_l */
  if (layers > 0) {
    const struct mhx_perm *yv = mhx_map_get(&Y_layers[layers - 1], a);
    if (yv && memcmp(yv->v, v, (size_t)h->n * sizeof(uint16_t)) == 0) return 0;
  }

  /* v not in x */
  {
    const struct mhx_perm_list *xs = mhx_xmap_get_const(x, a);
    if (xs && mhx_perm_list_contains(xs, v)) return 0;
  }

  /* v not in y */
  {
    const struct mhx_perm *yv = mhx_map_get(y, a);
    if (yv && memcmp(yv->v, v, (size_t)h->n * sizeof(uint16_t)) == 0) return 0;
  }

  /* |A(v) ^ X| < U */
  {
    const struct mhx_perm_list *xs = mhx_xmap_get_const(x, a);
    if (xs && (int)xs->len >= h->U) return 0;
  }

  struct mhx_perm vp = {.n = h->n, .v = (uint16_t *)v};

  /* no uv with u in y */
  for (size_t i = 0; i < y->len; i++) {
    if (mhx_color_eq(y->e[i].color, a, h->n)) continue;
    if (mhx_edge(h, &y->e[i].perm, &vp)) return 0;
  }

  /* no uv with u in x */
  for (size_t i = 0; i < x->len; i++) {
    const struct mhx_xentry *xe = &x->e[i];
    if (mhx_color_eq(xe->color, a, h->n)) continue;
    for (size_t j = 0; j < xe->set.len; j++) {
      if (mhx_edge(h, &xe->set.items[j], &vp)) return 0;
    }
  }

  /* no uv with u in Y<=l */
  for (size_t li = 0; li < layers; li++) {
    const struct mhx_map *yl = &Y_layers[li];
    for (size_t i = 0; i < yl->len; i++) {
      if (mhx_color_eq(yl->e[i].color, a, h->n)) continue;
      if (mhx_edge(h, &yl->e[i].perm, &vp)) return 0;
    }
  }

  /* no uv with u in X<=l */
  for (size_t li = 0; li < layers; li++) {
    const struct mhx_xmap *xl = &X_layers[li];
    for (size_t i = 0; i < xl->len; i++) {
      const struct mhx_xentry *xe = &xl->e[i];
      for (size_t j = 0; j < xe->set.len; j++) {
        if (mhx_color_eq(xe->color, a, h->n)) continue;
        if (mhx_edge(h, &xe->set.items[j], &vp)) return 0;
      }
    }
  }

  return 1;
}

static int mhx_from_color_find_addable(
    const struct mhx_haxell *h,
    const struct mhx_map *M,
    const struct mhx_xmap *X_layers,
    const struct mhx_map *Y_layers,
    size_t layers,
    const struct mhx_xmap *x,
    const struct mhx_map *y,
    const uint8_t *a,
    uint16_t *out_v /* n entries */) {
  int groups = h->groups;
  int *counters = (int *)calloc((size_t)groups, sizeof(int));
  if (!counters) return -1;

  for (size_t ri = 0; ri < h->ident_rows_count; ri++) {
    memset(counters, 0, (size_t)groups * sizeof(int));
    const uint16_t *row = h->ident_rows + (size_t)h->n * ri;
    for (int i = 0; i < h->n; i++) {
      int g = (int)a[i];
      int idx = counters[g] + h->d * g;
      out_v[i] = row[idx];
      counters[g]++;
    }

    if (mhx_is_addable(h, M, X_layers, Y_layers, layers, x, y, a, out_v)) {
      free(counters);
      return 1;
    }
  }

  free(counters);
  return 0;
}

static int mhx_find_addable(
    const struct mhx_haxell *h,
    const struct mhx_map *M,
    const struct mhx_xmap *X_layers,
    const struct mhx_map *Y_layers,
    size_t layers,
    const struct mhx_xmap *x,
    const struct mhx_map *y,
    const uint8_t *root_color,
    uint8_t *out_color /* n bytes */,
    uint16_t *out_v /* n entries */) {
  /* candidate colors: keys(Y_last) if layers>1 else [root_color] */
  if (layers <= 1) {
    memcpy(out_color, root_color, (size_t)h->n);
    int rc = mhx_from_color_find_addable(h, M, X_layers, Y_layers, layers, x, y, root_color, out_v);
    return rc;
  }

  const struct mhx_map *lastY = &Y_layers[layers - 1];
  for (size_t i = 0; i < lastY->len; i++) {
    const uint8_t *a = lastY->e[i].color;
    int rc = mhx_from_color_find_addable(h, M, X_layers, Y_layers, layers, x, y, a, out_v);
    if (rc < 0) return rc;
    if (rc == 1) {
      memcpy(out_color, a, (size_t)h->n);
      return 1;
    }
  }
  return 0;
}

static int mhx_build_layer(
    const struct mhx_haxell *h,
    const struct mhx_map *M,
    const struct mhx_xmap *X_layers,
    const struct mhx_map *Y_layers,
    size_t layers,
    const struct mhx_xmap *x_in,
    const struct mhx_map *y_in,
    const uint8_t *root_color,
    struct mhx_xmap *out_x,
    struct mhx_map *out_y) {
  struct mhx_xmap x = mhx_xmap_create(h->n);
  struct mhx_map y = mhx_map_create(h->n);
  if (x_in && x_in->len) {
    if (mhx_xmap_clone_into(x_in, &x) != 0) return -1;
  }
  if (y_in && y_in->len) {
    if (mhx_map_clone_into(y_in, &y) != 0) return -1;
  }

  uint8_t av[256];
  uint16_t vbuf[1024];
  if (h->n > (int)sizeof(av) || h->n > (int)(sizeof(vbuf) / sizeof(vbuf[0]))) {
    mhx_xmap_destroy(&x);
    mhx_map_destroy(&y);
    return -1;
  }

  for (;;) {
    int pot = mhx_find_addable(h, M, X_layers, Y_layers, layers, &x, &y, root_color, av, vbuf);
    if (pot < 0) {
      mhx_xmap_destroy(&x);
      mhx_map_destroy(&y);
      return -1;
    }
    if (pot == 0) break;

    if (mhx_xmap_add_perm(&x, av, vbuf) != 0) {
      mhx_xmap_destroy(&x);
      mhx_map_destroy(&y);
      return -1;
    }

    struct mhx_perm vp = {.n = h->n, .v = vbuf};
    for (size_t i = 0; i < M->len; i++) {
      if (mhx_color_eq(M->e[i].color, av, h->n)) continue;
      if (mhx_edge(h, &M->e[i].perm, &vp)) {
        if (mhx_map_set(&y, M->e[i].color, &M->e[i].perm) != 0) {
          mhx_xmap_destroy(&x);
          mhx_map_destroy(&y);
          return -1;
        }
      }
    }
  }

  *out_x = x;
  *out_y = y;
  return 0;
}

static int mhx_ident_rows_build(struct mhx_haxell *h) {
  int n = h->n;
  int d = h->d;
  int groups = h->groups;

  /* per-group permutations */
  size_t *counts = (size_t *)calloc((size_t)groups, sizeof(size_t));
  int *lens = (int *)calloc((size_t)groups, sizeof(int));
  uint16_t **perms = (uint16_t **)calloc((size_t)groups, sizeof(uint16_t *));
  if (!counts || !lens || !perms) {
    free(counts);
    free(lens);
    free(perms);
    return -1;
  }

  for (int g = 0; g < groups; g++) {
    int start = d * g;
    int end = d * (g + 1);
    if (end > n) end = n;
    int len = end - start;
    lens[g] = len;
    if (mhx_factorial(len, &counts[g]) != 0) goto fail;
    perms[g] = (uint16_t *)malloc(counts[g] * (size_t)len * sizeof(uint16_t));
    if (!perms[g]) goto fail;

    /* generate permutations of [start..end) into perms[g] */
    uint16_t base[64];
    if (len > (int)(sizeof(base) / sizeof(base[0]))) goto fail;
    for (int i = 0; i < len; i++) base[i] = (uint16_t)(start + i);

    size_t out_idx = 0;
    /* Heap's algorithm */
    int c[64];
    memset(c, 0, (size_t)len * sizeof(int));
    memcpy(perms[g] + out_idx * (size_t)len, base, (size_t)len * sizeof(uint16_t));
    out_idx++;
    int i = 0;
    while (i < len) {
      if (c[i] < i) {
        int a = (i % 2 == 0) ? 0 : c[i];
        uint16_t tmp = base[a];
        base[a] = base[i];
        base[i] = tmp;
        memcpy(perms[g] + out_idx * (size_t)len, base, (size_t)len * sizeof(uint16_t));
        out_idx++;
        c[i]++;
        i = 0;
      } else {
        c[i] = 0;
        i++;
      }
    }
    if (out_idx != counts[g]) goto fail;
  }

  size_t rows = 1;
  for (int g = 0; g < groups; g++) {
    if (counts[g] == 0 || rows > (SIZE_MAX / counts[g])) goto fail;
    rows *= counts[g];
  }

  h->ident_rows = (uint16_t *)malloc(rows * (size_t)n * sizeof(uint16_t));
  if (!h->ident_rows) goto fail;
  h->ident_rows_count = rows;

  uint16_t rowbuf[1024];
  if (n > (int)(sizeof(rowbuf) / sizeof(rowbuf[0]))) goto fail;

  size_t row_out = 0;
  /* Cartesian product over group permutations */
  size_t *idx = (size_t *)calloc((size_t)groups, sizeof(size_t));
  if (!idx) goto fail;
  for (;;) {
    /* build concatenated row */
    for (int g = 0; g < groups; g++) {
      int start = d * g;
      int len = lens[g];
      memcpy(rowbuf + start, perms[g] + idx[g] * (size_t)len, (size_t)len * sizeof(uint16_t));
    }
    memcpy(h->ident_rows + row_out * (size_t)n, rowbuf, (size_t)n * sizeof(uint16_t));
    row_out++;

    /* increment odometer */
    int carry = 1;
    for (int g = groups - 1; g >= 0 && carry; g--) {
      idx[g]++;
      if (idx[g] >= counts[g]) {
        idx[g] = 0;
        carry = 1;
      } else {
        carry = 0;
      }
    }
    if (carry) break;
  }
  free(idx);
  if (row_out != rows) goto fail;

  for (int g = 0; g < groups; g++) free(perms[g]);
  free(perms);
  free(counts);
  free(lens);
  return 0;

fail:
  if (perms) {
    for (int g = 0; g < groups; g++) free(perms[g]);
  }
  free(perms);
  free(counts);
  free(lens);
  return -1;
}

struct mhx_haxell *mhx_haxell_create(int n, int d, double eps) {
  if (n <= 0 || d <= 0) return NULL;

  struct mhx_haxell *h = (struct mhx_haxell *)calloc(1, sizeof(*h));
  if (!h) return NULL;
  h->n = n;
  h->d = d;
  h->groups = (int)ceil((double)n / (double)d);
  h->dsq = (uint64_t)d * (uint64_t)d;

  int r = 0;
  if (mhx_compute_r(n, d, &r) != 0) {
    free(h);
    return NULL;
  }
  if (mhx_feasible_constants(r, eps, &h->mu, &h->U, &h->rho) != 0) {
    free(h);
    return NULL;
  }

  if (mhx_ident_rows_build(h) != 0) {
    mhx_haxell_destroy(h);
    return NULL;
  }

  return h;
}

void mhx_haxell_destroy(struct mhx_haxell *h) {
  if (!h) return;
  free(h->ident_rows);
  free(h);
}

int mhx_grow_transversal(struct mhx_haxell *h, struct mhx_map *M, const uint8_t *A, struct mhx_map *diff) {
  if (!h || !M || !A || !diff) return -1;
  if (M->n != h->n) return -1;

  /* Reset diff. */
  mhx_map_destroy(diff);
  *diff = mhx_map_create(h->n);

  /* Layers */
  size_t cap = 8;
  size_t layers = 1;
  struct mhx_xmap *X = (struct mhx_xmap *)calloc(cap, sizeof(*X));
  struct mhx_map *Y = (struct mhx_map *)calloc(cap, sizeof(*Y));
  if (!X || !Y) {
    free(X);
    free(Y);
    return -1;
  }
  X[0] = mhx_xmap_create(h->n);
  Y[0] = mhx_map_create(h->n);

  size_t l = 0;
  while (mhx_map_get(M, A) == NULL) {
    struct mhx_xmap xpot;
    struct mhx_map ypot;
    if (mhx_build_layer(h, M, X, Y, layers, NULL, NULL, A, &xpot, &ypot) != 0) {
      goto fail;
    }

    if (layers == cap) {
      size_t new_cap = cap * 2;
      struct mhx_xmap *X2 = (struct mhx_xmap *)realloc(X, new_cap * sizeof(*X));
      struct mhx_map *Y2 = (struct mhx_map *)realloc(Y, new_cap * sizeof(*Y));
      if (!X2 || !Y2) {
        mhx_xmap_destroy(&xpot);
        mhx_map_destroy(&ypot);
        goto fail;
      }
      X = X2;
      Y = Y2;
      cap = new_cap;
    }
    X[layers] = xpot;
    Y[layers] = ypot;
    layers++;

    size_t xpot_len = mhx_xmap_total_len(&xpot);
    size_t y_sum = 0;
    for (size_t i = 0; i <= l; i++) y_sum += Y[i].len;
    if ((double)xpot_len <= h->rho * (double)y_sum) {
      /* fail */
      for (size_t i = 0; i < layers; i++) {
        mhx_xmap_destroy(&X[i]);
        mhx_map_destroy(&Y[i]);
      }
      free(X);
      free(Y);
      return 0;
    }

    l++;

    for (;;) {
      struct mhx_xmap I;
      if (mhx_immediately_addable(h, M, &X[l], &I) != 0) goto fail;

      size_t I_len = mhx_xmap_total_len(&I);
      size_t Xl_len = mhx_xmap_total_len(&X[l]);
      if ((double)I_len <= h->mu * (double)Xl_len) {
        mhx_xmap_destroy(&I);
        break;
      }

      if (l == 1) {
        /* augment one row */
        for (size_t ei = 0; ei < I.len; ei++) {
          const struct mhx_xentry *e = &I.e[ei];
          for (size_t pj = 0; pj < e->set.len; pj++) {
            const struct mhx_perm *u = &e->set.items[pj];
            if (mhx_map_set(diff, e->color, u) != 0) {
              mhx_xmap_destroy(&I);
              goto fail;
            }
            if (mhx_map_set(M, e->color, u) != 0) {
              mhx_xmap_destroy(&I);
              goto fail;
            }
            mhx_xmap_destroy(&I);
            for (size_t i = 0; i < layers; i++) {
              mhx_xmap_destroy(&X[i]);
              mhx_map_destroy(&Y[i]);
            }
            free(X);
            free(Y);
            return 1;
          }
        }
        mhx_xmap_destroy(&I);
        goto fail;
      }

      /* switch around Y[l-1] vertices */
      struct mhx_map *Yprev = &Y[l - 1];
      uint8_t *keys = (uint8_t *)malloc((size_t)Yprev->len * (size_t)h->n);
      if (!keys) {
        mhx_xmap_destroy(&I);
        goto fail;
      }
      for (size_t ki = 0; ki < Yprev->len; ki++) {
        memcpy(keys + ki * (size_t)h->n, Yprev->e[ki].color, (size_t)h->n);
      }

      for (size_t ki = 0; ki < Yprev->len; ki++) {
        uint8_t *aw = keys + ki * (size_t)h->n;
        const struct mhx_perm_list *Iset = mhx_xmap_get_const(&I, aw);
        if (!Iset || Iset->len == 0) continue;
        const struct mhx_perm *u = &Iset->items[0];

        if (mhx_map_set(diff, aw, u) != 0) {
          free(keys);
          mhx_xmap_destroy(&I);
          goto fail;
        }
        if (mhx_map_set(M, aw, u) != 0) {
          free(keys);
          mhx_xmap_destroy(&I);
          goto fail;
        }
        (void)mhx_map_del(Yprev, aw);

        mhx_xmap_destroy(&I);
        if (mhx_immediately_addable(h, M, &X[l], &I) != 0) {
          free(keys);
          goto fail;
        }
      }
      free(keys);
      mhx_xmap_destroy(&I);

      /* truncate layers to l (keep 0..l-1), then l -= 1 */
      mhx_xmap_destroy(&X[l]);
      mhx_map_destroy(&Y[l]);
      layers = l;
      l--;

      /* superposed_build */
      for (size_t i = 1; i <= l; i++) {
        struct mhx_xmap xprime;
        struct mhx_map yprime;
        if (mhx_build_layer(h, M, X, Y, i, &X[i], &Y[i], A, &xprime, &yprime) != 0) goto fail;
        size_t xprime_len = mhx_xmap_total_len(&xprime);
        size_t x_len = mhx_xmap_total_len(&X[i]);
        if ((double)xprime_len >= (1.0 + h->mu) * (double)x_len) {
          mhx_xmap_destroy(&X[i]);
          mhx_map_destroy(&Y[i]);
          X[i] = xprime;
          Y[i] = yprime;
          l = i;
        } else {
          mhx_xmap_destroy(&xprime);
          mhx_map_destroy(&yprime);
        }
      }

      /* after superposed_build, continue inner loop */
      layers = l + 1;
    }
  }

  for (size_t i = 0; i < layers; i++) {
    mhx_xmap_destroy(&X[i]);
    mhx_map_destroy(&Y[i]);
  }
  free(X);
  free(Y);
  return diff->len ? 1 : 0;

fail:
  for (size_t i = 0; i < layers; i++) {
    mhx_xmap_destroy(&X[i]);
    mhx_map_destroy(&Y[i]);
  }
  free(X);
  free(Y);
  return -1;
}
