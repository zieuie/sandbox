// Symmetry-reduced template climb, general block templates.
//
// Blocks: value v lies in block v / d.  Template of a row = block index at
// each position.  Same template => never separated, so an array has at most
// one row per template; we look for one row per template (the multinomial
// count), which equals the conjectured C(n,d) P(n-d,d) when n = qd (and for
// (8,3), (11,3), ...).
//
// Only arrays invariant under a group G acting freely on templates are
// searched; one representative row per orbit is stored.
//   group "cyc": shifts x -> x+b mod (n-1), position n-1 fixed
//   group "dih": also (value reversal, x -> -x+b)   [needs symmetric blocks]
//   group "none"
//
// Usage: symclimb2 n d seconds seed group temp
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

#define MAXN 20
#define MAXG 64

static int n, d, q, NG, R;
static int gt[MAXG], gp[MAXG][MAXN];
static int8_t (*rep)[MAXN], (*best_rep)[MAXN];
static int8_t (*img)[MAXG][MAXN];
static int8_t (*tmpl)[MAXN];     // rep templates
static int *foe_start, *foe_k, *foe_h, nfoe; // foe list per rep: (k,h) pairs
static int *conf;                 // conflicts seen from rep j
static long long T;
static uint64_t rs;
static inline uint64_t xr(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static inline double ur(void) { return (xr() >> 11) * (1.0 / 9007199254740992.0); }

static inline void act(int h, const int8_t *r, int8_t *out) {
  for (int i = 0; i < n; i++) out[gp[h][i]] = gt[h] ? (int8_t) (n - 1 - r[i]) : r[i];
}
static inline int sep(const int8_t *a, const int8_t *b) {
  for (int i = 0; i < n; i++) { int x = a[i] - b[i]; if (x >= d || -x >= d) return 1; }
  return 0;
}
static void act_t(int h, const int8_t *t, int8_t *out) {
  for (int i = 0; i < n; i++) out[gp[h][i]] = gt[h] ? (int8_t) (q - 1 - t[i]) : t[i];
}
static int bsize(int b) { int lo = b * d, hi = (b + 1) * d; if (hi > n) hi = n; return hi - lo; }

// encode template as 64-bit (base 4 digits ok since q <= 7 needs 3 bits)
static uint64_t enc(const int8_t *t) { uint64_t x = 0; for (int i = 0; i < n; i++) x = x * 8 + t[i]; return x; }

typedef struct { uint64_t key; int val; } hent;
static hent *ht; static size_t hcap;
static int hget(uint64_t k) { size_t i = (k * 0x9E3779B97F4A7C15ULL) & (hcap - 1); while (ht[i].val >= 0) { if (ht[i].key == k) return ht[i].val; i = (i + 1) & (hcap - 1); } return -1; }
static void hput(uint64_t k, int v) { size_t i = (k * 0x9E3779B97F4A7C15ULL) & (hcap - 1); while (ht[i].val >= 0) { if (ht[i].key == k) { ht[i].val = v; return; } i = (i + 1) & (hcap - 1); } ht[i].key = k; ht[i].val = v; }

static int8_t cur[MAXN]; static int cnt[MAXN];
static int8_t (*alltmpl)[MAXN]; static int ntempl;
static void gen(int pos) {
  if (pos == n) { memcpy(alltmpl[ntempl++], cur, n); return; }
  for (int b = 0; b < q; b++) if (cnt[b] < bsize(b)) { cnt[b]++; cur[pos] = b; gen(pos + 1); cnt[b]--; }
}

static void fill(const int8_t *t, int8_t *r) {
  int vals[MAXN][MAXN], nv[MAXN];
  for (int b = 0; b < q; b++) {
    nv[b] = 0;
    for (int v = b * d; v < b * d + bsize(b); v++) vals[b][nv[b]++] = v;
    for (int i = nv[b] - 1; i > 0; i--) { int j = xr() % (i + 1); int x = vals[b][i]; vals[b][i] = vals[b][j]; vals[b][j] = x; }
  }
  int used[MAXN] = {0};
  for (int i = 0; i < n; i++) r[i] = (int8_t) vals[t[i]][used[t[i]]++];
}

static void make_images(int j) { for (int h = 0; h < NG; h++) act(h, rep[j], img[j][h]); }
static int count_conf(int j, const int8_t *row) {
  int c = 0;
  for (int f = foe_start[j]; f < foe_start[j + 1]; f++) {
    int k = foe_k[f], h = foe_h[f];
    const int8_t *other = (k == j) ? NULL : img[k][h];
    int8_t tmp[MAXN];
    if (k == j) { act(h, row, tmp); other = tmp; }
    if (!sep(row, other)) c++;
  }
  return c;
}
static void full_score(void) { T = 0; for (int j = 0; j < R; j++) { conf[j] = count_conf(j, rep[j]); T += conf[j]; } }

int main(int argc, char **argv) {
  if (argc < 7) { fprintf(stderr, "usage: %s n d seconds seed cyc|dih|none temp\n", argv[0]); return 1; }
  n = atoi(argv[1]); d = atoi(argv[2]); int secs = atoi(argv[3]);
  rs = strtoull(argv[4], 0, 10) * 6364136223846793005ULL + 1442695040888963407ULL; if (!rs) rs = 1;
  const char *gname = argv[5]; double temp = atof(argv[6]);
  q = (n + d - 1) / d;
  // group
  NG = 0;
  int m = n - 1;
  if (!strcmp(gname, "none")) { gt[0] = 0; for (int i = 0; i < n; i++) gp[0][i] = i; NG = 1; }
  else for (int s = 0; s < (strcmp(gname, "dih") ? 1 : 2); s++) for (int b = 0; b < m; b++) {
    gt[NG] = s; for (int x = 0; x < m; x++) gp[NG][x] = (((s ? -x : x) + b) % m + m) % m; gp[NG][m] = m; NG++;
  }
  if (!strcmp(gname, "dih")) for (int b = 0; b < q; b++) if (bsize(b) != bsize(q - 1 - b)) { fprintf(stderr, "blocks not symmetric; use cyc\n"); return 1; }
  // templates
  long long mult = 1; { long long f = 1; for (int i = 2; i <= n; i++) f *= i; mult = f; for (int b = 0; b < q; b++) { long long g = 1; for (int i = 2; i <= bsize(b); i++) g *= i; mult /= g; } }
  alltmpl = malloc(sizeof(*alltmpl) * mult);
  gen(0);
  hcap = 1; while (hcap < (size_t) ntempl * 3) hcap <<= 1;
  ht = malloc(sizeof(hent) * hcap); for (size_t i = 0; i < hcap; i++) ht[i].val = -1;
  tmpl = malloc(sizeof(*tmpl) * ntempl);
  // orbit reps; ht maps template -> (rep index * NG + h)
  R = 0;
  for (int t = 0; t < ntempl; t++) {
    if (hget(enc(alltmpl[t])) >= 0) continue;
    int8_t im[MAXN];
    for (int h = 0; h < NG; h++) {
      act_t(h, alltmpl[t], im);
      uint64_t k = enc(im);
      if (hget(k) >= 0) { fprintf(stderr, "group not free on templates\n"); return 1; }
      hput(k, R * NG + h);
    }
    memcpy(tmpl[R++], alltmpl[t], n);
  }
  printf("P(%d,%d): blocks=%d, |G|=%d, templates=%d, reps=%d\n", n, d, q, NG, ntempl, R); fflush(stdout);
  // foe lists: for rep j, all (k,h) with template within 1 everywhere, excluding (j, identity)
  foe_start = malloc(sizeof(int) * (R + 1));
  size_t cap = 1 << 20; foe_k = malloc(sizeof(int) * cap); foe_h = malloc(sizeof(int) * cap); nfoe = 0;
  for (int j = 0; j < R; j++) {
    foe_start[j] = nfoe;
    for (int t = 0; t < ntempl; t++) {
      int ok = 1, same = 1;
      for (int i = 0; i < n; i++) { int x = tmpl[j][i] - alltmpl[t][i]; if (x > 1 || x < -1) { ok = 0; break; } if (x) same = 0; }
      if (!ok || same) continue;
      int v = hget(enc(alltmpl[t]));
      if ((size_t) nfoe == cap) { cap *= 2; foe_k = realloc(foe_k, sizeof(int) * cap); foe_h = realloc(foe_h, sizeof(int) * cap); }
      foe_k[nfoe] = v / NG; foe_h[nfoe] = v % NG; nfoe++;
    }
  }
  foe_start[R] = nfoe;
  printf("foe entries per rep: %.1f\n", (double) nfoe / R); fflush(stdout);
  rep = malloc(sizeof(*rep) * R); best_rep = malloc(sizeof(*best_rep) * R);
  img = malloc(sizeof(*img) * R); conf = malloc(sizeof(int) * R);
  for (int j = 0; j < R; j++) { fill(tmpl[j], rep[j]); make_images(j); }
  full_score();
  long long best = T; memcpy(best_rep, rep, sizeof(*rep) * R);
  time_t start = time(NULL), last_print = start;
  long long it = 0, last_improve = 0;
  int *dk = calloc(R, sizeof(int));
  int8_t cand[MAXN], cimg[MAXG][MAXN];
  while (T > 0 && time(NULL) - start < secs) {
    it++;
    int j = xr() % R;
    if (conf[j] == 0 && (xr() % 4)) continue;
    memcpy(cand, rep[j], n);
    int blk = xr() % q, pos[MAXN], np = 0;
    for (int i = 0; i < n; i++) if (tmpl[j][i] == blk) pos[np++] = i;
    if (np < 2) continue;
    if (xr() & 1) { int a = xr() % np, b = xr() % (np - 1); if (b >= a) b++; int8_t x = cand[pos[a]]; cand[pos[a]] = cand[pos[b]]; cand[pos[b]] = x; }
    else for (int i = np - 1; i > 0; i--) { int k = xr() % (i + 1); int8_t x = cand[pos[i]]; cand[pos[i]] = cand[pos[k]]; cand[pos[k]] = x; }
    for (int h = 0; h < NG; h++) act(h, cand, cimg[h]);
    long long delta = 0; int newself = 0;
    for (int f = foe_start[j]; f < foe_start[j + 1]; f++) {
      int k = foe_k[f], h = foe_h[f];
      int oldc, newc;
      if (k == j) { oldc = !sep(rep[j], img[j][h]); newc = !sep(cand, cimg[h]); newself += newc; delta += newc - oldc; }
      else { oldc = !sep(rep[j], img[k][h]); newc = !sep(cand, img[k][h]); dk[k] += newc - oldc; delta += 2 * (newc - oldc); }
    }
    int accept = delta <= 0 || ur() < exp(-delta / temp);
    for (int f = foe_start[j]; f < foe_start[j + 1]; f++) {
      int k = foe_k[f];
      if (k != j && dk[k]) { if (accept) conf[k] += dk[k]; dk[k] = 0; }
    }
    if (accept) {
      // conf[j] recomputed: own-orbit + cross
      memcpy(rep[j], cand, n); memcpy(img[j], cimg, sizeof(int8_t) * MAXN * NG);
      conf[j] = count_conf(j, rep[j]);
      T += delta;
      if (T < best) { best = T; last_improve = it; memcpy(best_rep, rep, sizeof(*rep) * R); }
    }
    if (it - last_improve > 5000000) {
      memcpy(rep, best_rep, sizeof(*rep) * R);
      for (int z = 0; z < 3; z++) { int jj = xr() % R; fill(tmpl[jj], rep[jj]); }
      for (int jj = 0; jj < R; jj++) make_images(jj);
      full_score(); last_improve = it;
    }
    if (time(NULL) - last_print >= 30) { last_print = time(NULL); printf("[%lds] it=%lld score=%lld best=%lld\n", (long) (last_print - start), it, T, best); fflush(stdout); }
  }
  printf("finished: best %lld after %lld moves, %lds\n", best, it, (long) (time(NULL) - start));
  char fn[64]; sprintf(fn, best == 0 ? "pa_%d_%d_sym.txt" : "pa_%d_%d_best.txt", n, d);
  FILE *f = fopen(fn, "w"); int8_t row[MAXN];
  for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) { act(h, best_rep[j], row); for (int i = 0; i < n; i++) fprintf(f, "%d ", row[i]); fprintf(f, "\n"); }
  fclose(f); printf("wrote %s (%d rows)\n", fn, R * NG);
  return 0;
}
