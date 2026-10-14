// Symmetry-reduced template hill climb for P(n,d) = C(n,d), n in {2d-1, 2d}.
//
// Template of a row = set U of positions holding the values 0..d-1.
// We look only for arrays invariant under a group G acting by
//     (tau, g) . r  =  tau o r o g^{-1}
// where g permutes positions and tau is the identity or value reversal
// v -> n-1-v.  G is chosen to act freely on templates, so every orbit has
// |G| templates and we only store one representative row per orbit.
//
//   n = 2d   : G = { x -> +-x + b  mod (n-1) } on positions 0..n-2, fixing
//              position n-1; the "-" elements also reverse values.
//              |G| = 2(2d-1).
//   n = 2d-1 : G = Z_n (cyclic shifts of positions). |G| = n.
//
// Usage: symclimb n d seconds [seed] [nogroup]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

#define MAXN 20
#define MAXG 64
#define MAXR 1024

static int n, d, NG, R;
static int gt[MAXG];            // tau: 0 = id, 1 = value reversal
static int gp[MAXG][MAXN];      // position permutation g
static int rep_mask[MAXR];
static int8_t rep[MAXR][MAXN];  // representative rows
static int8_t img[MAXR][MAXG][MAXN];
static int cross[MAXR][MAXR];   // cross[j][k] = # h: rep j vs h.rep k conflict (k != j)
static int selfc[MAXR];
static long long T;

static uint64_t rs;
static inline uint64_t xr(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static inline double ur(void) { return (xr() >> 11) * (1.0 / 9007199254740992.0); }

static void act(int h, const int8_t *r, int8_t *out) {
  for (int i = 0; i < n; i++) out[gp[h][i]] = gt[h] ? (int8_t) (n - 1 - r[i]) : r[i];
}
static int tmask(const int8_t *r) { int m = 0; for (int i = 0; i < n; i++) if (r[i] < d) m |= 1 << i; return m; }
static inline int sep(const int8_t *a, const int8_t *b) {
  for (int i = 0; i < n; i++) { int x = a[i] - b[i]; if (x >= d || -x >= d) return 1; }
  return 0;
}

static void build_group(int use_group) {
  NG = 0;
  if (!use_group) { gt[0] = 0; for (int i = 0; i < n; i++) gp[0][i] = i; NG = 1; return; }
  if (n == 2 * d) {
    int m = n - 1;
    for (int s = 0; s < (use_group == 2 ? 1 : 2); s++)
      for (int b = 0; b < m; b++) {
        gt[NG] = s;
        for (int x = 0; x < m; x++) gp[NG][x] = (((s ? -x : x) + b) % m + m) % m;
        gp[NG][m] = m;
        NG++;
      }
  } else {
    for (int b = 0; b < n; b++) { gt[NG] = 0; for (int x = 0; x < n; x++) gp[NG][x] = (x + b) % n; NG++; }
  }
}

// template orbit of mask under the group
static int act_mask(int h, int mask) {
  int out = 0;
  for (int i = 0; i < n; i++) if (mask >> i & 1) out |= 1 << gp[h][i];
  if (gt[h]) out = ((1 << n) - 1) & ~out; // reversal swaps low/high positions (n = 2d only)
  return out;
}

static void random_fill(int mask, int8_t *r) {
  int lo[MAXN], hi[MAXN], nl = 0, nh = 0;
  for (int v = 0; v < n; v++) { if (v < d) lo[nl++] = v; else hi[nh++] = v; }
  // shuffle values
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
    for (int h = 1; h < NG; h++) if (!sep(rep[j], img[j][h])) selfc[j]++;
    T += selfc[j];
    for (int k = 0; k < R; k++) {
      if (k == j) continue;
      int c = 0;
      for (int h = 0; h < NG; h++) if (!sep(rep[j], img[k][h])) c++;
      cross[j][k] = c;
      T += c;
    }
  }
}

int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: %s n d seconds [seed] [nogroup]\n", argv[0]); return 1; }
  n = atoi(argv[1]); d = atoi(argv[2]);
  int secs = atoi(argv[3]);
  rs = argc > 4 ? strtoull(argv[4], 0, 10) : (uint64_t) time(NULL);
  rs = rs * 6364136223846793005ULL + 1442695040888963407ULL; if (!rs) rs = 1;
  int use_group = 1;
  if (argc > 5 && !strcmp(argv[5], "nogroup")) use_group = 0;
  if (argc > 5 && !strcmp(argv[5], "cyc")) use_group = 2;
  double temp0 = argc > 6 ? atof(argv[6]) : 0.6;
  if (!(n == 2 * d || n == 2 * d - 1)) { fprintf(stderr, "need n = 2d or 2d-1\n"); return 1; }
  build_group(use_group);

  // orbits of templates
  static char seen[1 << MAXN];
  memset(seen, 0, sizeof seen);
  R = 0;
  int ntempl = 0;
  for (int mask = 0; mask < (1 << n); mask++) {
    if (__builtin_popcount(mask) != d) continue;
    ntempl++;
    if (seen[mask]) continue;
    int osize = 0;
    for (int h = 0; h < NG; h++) {
      int m2 = act_mask(h, mask);
      if (!seen[m2]) { seen[m2] = 1; osize++; }
    }
    if (osize != NG) { fprintf(stderr, "group not free on templates (orbit %d)\n", osize); return 1; }
    rep_mask[R++] = mask;
  }
  printf("P(%d,%d): |G| = %d, templates = %d, orbit reps = %d\n", n, d, NG, ntempl, R);
  fflush(stdout);

  for (int j = 0; j < R; j++) { random_fill(rep_mask[j], rep[j]); make_images(j); }
  full_score();
  long long best = T;
  static int8_t best_rep[MAXR][MAXN];
  memcpy(best_rep, rep, sizeof(int8_t) * MAXN * R);

  time_t start = time(NULL), last_print = start;
  double temp = temp0;
  long long it = 0, last_improve = 0;
  static int newc[MAXR];
  int8_t cand[MAXN], cimg[MAXG][MAXN];
  while (T > 0 && time(NULL) - start < secs) {
    it++;
    // pick a rep: mostly conflicted ones
    int j;
    j = xr() % R;
    // move: swap two positions of the same block, or shuffle a block
    memcpy(cand, rep[j], n);
    int blk = xr() & 1;
    int pos[MAXN], np = 0;
    for (int i = 0; i < n; i++) if ((cand[i] < d) == (blk == 0)) pos[np++] = i;
    if (np < 2) continue;
    if (xr() & 1) {
      int a = xr() % np, b = xr() % (np - 1); if (b >= a) b++;
      int8_t t = cand[pos[a]]; cand[pos[a]] = cand[pos[b]]; cand[pos[b]] = t;
    } else {
      for (int i = np - 1; i > 0; i--) { int k = xr() % (i + 1); int8_t t = cand[pos[i]]; cand[pos[i]] = cand[pos[k]]; cand[pos[k]] = t; }
    }
    for (int h = 0; h < NG; h++) act(h, cand, cimg[h]);
    int ns = 0;
    for (int h = 1; h < NG; h++) if (!sep(cand, cimg[h])) ns++;
    long long delta = ns - selfc[j];
    for (int k = 0; k < R; k++) {
      if (k == j) continue;
      int c = 0;
      for (int h = 0; h < NG; h++) if (!sep(cand, img[k][h])) c++;
      newc[k] = c;
      delta += 2LL * (c - cross[j][k]);
    }
    if (delta <= 0 || ur() < exp(-delta / temp)) {
      memcpy(rep[j], cand, n);
      memcpy(img[j], cimg, sizeof(int8_t) * MAXN * NG);
      selfc[j] = ns;
      for (int k = 0; k < R; k++) if (k != j) { cross[j][k] = newc[k]; cross[k][j] = newc[k]; }
      T += delta;
      if (T < best) { best = T; last_improve = it; memcpy(best_rep, rep, sizeof(int8_t) * MAXN * R); }
    }
    if (it - last_improve > 2000000) { // restart from best with a kick
      memcpy(rep, best_rep, sizeof(int8_t) * MAXN * R);
      for (int q = 0; q < 3; q++) { int jj = xr() % R; random_fill(rep_mask[jj], rep[jj]); }
      for (int jj = 0; jj < R; jj++) make_images(jj);
      full_score();
      last_improve = it;
    }
    if (time(NULL) - last_print >= 10) {
      last_print = time(NULL);
      printf("[%lds] it=%lld score=%lld best=%lld\n", (long) (last_print - start), it, T, best);
      fflush(stdout);
    }
  }
  printf("finished: best score %lld after %lld moves, %lds\n", best, it, (long) (time(NULL) - start));
  if (best == 0) {
    char fn[64];
    sprintf(fn, "pa_%d_%d_sym.txt", n, d);
    FILE *f = fopen(fn, "w");
    int8_t row[MAXN];
    for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) {
      act(h, best_rep[j], row);
      for (int i = 0; i < n; i++) fprintf(f, "%d ", row[i]);
      fprintf(f, "\n");
    }
    fclose(f);
    printf("wrote %s (%d rows)\n", fn, R * NG);
  }
  return 0;
}
