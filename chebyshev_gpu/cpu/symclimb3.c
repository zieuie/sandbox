// symclimb3: same search as symclimb (two blocks, n = 2d or 2d-1, group of
// order 2(2d-1) or n acting freely on templates), but with incremental
// evaluation.  For every rep j, rep k and group element h we keep
//     sc[j][k][h] = number of positions where rep j and h.rep k differ by >= d
// (pair conflicts iff sc == 0).  A move swaps two positions a, b of one rep,
// so only those two positions need re-checking against every other row.
// By isometry, sc[k][j][h] = sc[j][k][inv(h)].
//
// Usage: symclimb3 n d seconds seed temp [resume_file]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

#define MAXN 20
#define MAXG 64
static int n, d, NG, R;
static int gt[MAXG], gp[MAXG][MAXN], ginv[MAXG];
static int *rep_mask;
static int8_t (*rep)[MAXN], (*best_rep)[MAXN];
static int8_t (*img)[MAXG][MAXN];
static uint8_t *sc;     // R*R*NG
static int *selfc;
static long long T;
static uint64_t rs;
static inline uint64_t xr(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static inline double ur(void) { return (xr() >> 11) * (1.0 / 9007199254740992.0); }
#define SC(j, k, h) sc[((size_t) (j) * R + (k)) * NG + (h)]

static void act(int h, const int8_t *r, int8_t *out) { for (int i = 0; i < n; i++) out[gp[h][i]] = gt[h] ? (int8_t) (n - 1 - r[i]) : r[i]; }
static int act_mask(int h, int mask) { int o = 0; for (int i = 0; i < n; i++) if (mask >> i & 1) o |= 1 << gp[h][i]; if (gt[h]) o = ((1 << n) - 1) & ~o; return o; }
static inline int nsep(const int8_t *a, const int8_t *b) { int c = 0; for (int i = 0; i < n; i++) { int x = a[i] - b[i]; c += (x >= d || -x >= d); } return c; }
static inline int far(int x, int y) { int z = x - y; return z >= d || -z >= d; }

static void random_fill(int mask, int8_t *r) {
  int lo[MAXN], hi[MAXN], nl = 0, nh = 0;
  for (int v = 0; v < n; v++) { if (v < d) lo[nl++] = v; else hi[nh++] = v; }
  for (int i = nl - 1; i > 0; i--) { int j = xr() % (i + 1); int t = lo[i]; lo[i] = lo[j]; lo[j] = t; }
  for (int i = nh - 1; i > 0; i--) { int j = xr() % (i + 1); int t = hi[i]; hi[i] = hi[j]; hi[j] = t; }
  int a = 0, b = 0;
  for (int i = 0; i < n; i++) r[i] = (int8_t) ((mask >> i & 1) ? lo[a++] : hi[b++]);
}
static void make_images(int j) { for (int h = 0; h < NG; h++) act(h, rep[j], img[j][h]); }
static void full_score(void) {
  T = 0;
  for (int j = 0; j < R; j++) {
    selfc[j] = 0;
    for (int h = 1; h < NG; h++) if (!nsep(rep[j], img[j][h])) selfc[j]++;
    T += selfc[j];
    for (int k = 0; k < R; k++) for (int h = 0; h < NG; h++) {
      if (k == j) { SC(j, k, h) = 0; continue; }
      SC(j, k, h) = (uint8_t) nsep(rep[j], img[k][h]);
      if (!SC(j, k, h)) T++;
    }
  }
}

int main(int argc, char **argv) {
  if (argc < 6) { fprintf(stderr, "usage: %s n d seconds seed temp [resume]\n", argv[0]); return 1; }
  n = atoi(argv[1]); d = atoi(argv[2]); int secs = atoi(argv[3]);
  rs = strtoull(argv[4], 0, 10) * 6364136223846793005ULL + 1442695040888963407ULL; if (!rs) rs = 1;
  double temp = atof(argv[5]);
  if (n == 2 * d) { int m = n - 1; NG = 0; for (int s = 0; s < 2; s++) for (int b = 0; b < m; b++) { gt[NG] = s; for (int x = 0; x < m; x++) gp[NG][x] = (((s ? -x : x) + b) % m + m) % m; gp[NG][m] = m; NG++; } }
  else if (n == 2 * d - 1) { NG = 0; for (int b = 0; b < n; b++) { gt[NG] = 0; for (int x = 0; x < n; x++) gp[NG][x] = (x + b) % n; NG++; } }
  else { fprintf(stderr, "need n = 2d or 2d-1\n"); return 1; }
  // inverses
  for (int h = 0; h < NG; h++) for (int g = 0; g < NG; g++) {
    if (gt[h] != gt[g]) continue;
    int ok = 1; for (int i = 0; i < n; i++) if (gp[g][gp[h][i]] != i) { ok = 0; break; }
    if (ok) { ginv[h] = g; break; }
  }
  char *seen = calloc(1 << n, 1);
  rep_mask = malloc(sizeof(int) * (1 << n) / 2);
  R = 0;
  for (int mask = 0; mask < (1 << n); mask++) {
    if (__builtin_popcount(mask) != d || seen[mask]) continue;
    int os = 0; for (int h = 0; h < NG; h++) { int m2 = act_mask(h, mask); if (!seen[m2]) { seen[m2] = 1; os++; } }
    if (os != NG) { fprintf(stderr, "not free\n"); return 1; }
    rep_mask[R++] = mask;
  }
  printf("P(%d,%d): |G|=%d reps=%d rows=%d\n", n, d, NG, R, R * NG); fflush(stdout);
  rep = malloc(sizeof(*rep) * R); best_rep = malloc(sizeof(*rep) * R); img = malloc(sizeof(*img) * R);
  sc = malloc((size_t) R * R * NG); selfc = malloc(sizeof(int) * R);
  for (int j = 0; j < R; j++) random_fill(rep_mask[j], rep[j]);
  if (argc > 6) { // resume: file of R*NG rows in rep-major order, take the first row of each orbit
    FILE *f = fopen(argv[6], "r"); if (!f) { perror("resume"); return 1; }
    for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) { int8_t row[MAXN]; for (int i = 0; i < n; i++) { int v; if (fscanf(f, "%d", &v) != 1) { fprintf(stderr, "bad resume\n"); return 1; } row[i] = v; } if (h == 0) memcpy(rep[j], row, n); }
    fclose(f); printf("resumed from %s\n", argv[6]);
  }
  for (int j = 0; j < R; j++) make_images(j);
  full_score();
  long long best = T; memcpy(best_rep, rep, sizeof(*rep) * R);
  time_t start = time(NULL), last_print = start, last_save = start;
  long long it = 0, last_improve = 0;
  int8_t cand[MAXN], cimg[MAXG][MAXN];
  uint8_t *newsc = malloc((size_t) R * NG);
  while (T > 0 && time(NULL) - start < secs) {
    it++;
    int j = xr() % R;
    int blk = xr() & 1, pos[MAXN], np = 0;
    for (int i = 0; i < n; i++) if ((rep[j][i] < d) == (blk == 0)) pos[np++] = i;
    if (np < 2) continue;
    int ia = xr() % np, ib = xr() % (np - 1); if (ib >= ia) ib++;
    int a = pos[ia], b = pos[ib];
    memcpy(cand, rep[j], n); cand[a] = rep[j][b]; cand[b] = rep[j][a];
    long long delta = 0;
    // cross pairs: only positions a, b change on rep j's side
    for (int k = 0; k < R; k++) {
      if (k == j) continue;
      for (int h = 0; h < NG; h++) {
        const int8_t *o = img[k][h];
        int s = SC(j, k, h) - far(rep[j][a], o[a]) - far(rep[j][b], o[b]) + far(cand[a], o[a]) + far(cand[b], o[b]);
        newsc[k * NG + h] = (uint8_t) s;
        delta += 2 * ((s == 0) - (SC(j, k, h) == 0));
      }
    }
    for (int h = 0; h < NG; h++) act(h, cand, cimg[h]);
    int ns = 0; for (int h = 1; h < NG; h++) if (!nsep(cand, cimg[h])) ns++;
    delta += ns - selfc[j];
    if (delta <= 0 || ur() < exp(-delta / temp)) {
      memcpy(rep[j], cand, n); memcpy(img[j], cimg, sizeof(int8_t) * MAXN * NG);
      selfc[j] = ns;
      for (int k = 0; k < R; k++) { if (k == j) continue; for (int h = 0; h < NG; h++) { uint8_t s = newsc[k * NG + h]; SC(j, k, h) = s; SC(k, j, ginv[h]) = s; } }
      T += delta;
      if (T < best) { best = T; last_improve = it; memcpy(best_rep, rep, sizeof(*rep) * R); }
    }
    if (it - last_improve > 20000000) {
      memcpy(rep, best_rep, sizeof(*rep) * R);
      for (int z = 0; z < 2; z++) { int jj = xr() % R; random_fill(rep_mask[jj], rep[jj]); }
      for (int jj = 0; jj < R; jj++) make_images(jj);
      full_score(); last_improve = it;
    }
    if (time(NULL) - last_print >= 60) { last_print = time(NULL); printf("[%lds] it=%lld score=%lld best=%lld\n", (long) (last_print - start), it, T, best); fflush(stdout); }
    if (time(NULL) - last_save >= 600) { // checkpoint best
      last_save = time(NULL);
      char fn[64]; sprintf(fn, "pa_%d_%d_ckpt.txt", n, d); FILE *f = fopen(fn, "w"); int8_t row[MAXN];
      for (int jj = 0; jj < R; jj++) for (int h = 0; h < NG; h++) { act(h, best_rep[jj], row); for (int i = 0; i < n; i++) fprintf(f, "%d ", row[i]); fprintf(f, "\n"); }
      fclose(f);
    }
  }
  printf("finished: best %lld after %lld moves, %lds\n", best, it, (long) (time(NULL) - start));
  char fn[64]; sprintf(fn, best == 0 ? "pa_%d_%d_sym.txt" : "pa_%d_%d_best.txt", n, d);
  FILE *f = fopen(fn, "w"); int8_t row[MAXN];
  for (int jj = 0; jj < R; jj++) for (int h = 0; h < NG; h++) { act(h, best_rep[jj], row); for (int i = 0; i < n; i++) fprintf(f, "%d ", row[i]); fprintf(f, "\n"); }
  fclose(f); printf("wrote %s\n", fn);
  return 0;
}
