// Exact test for a (7,4)-PA of size 35 with arc-consistency propagation.
// Forced structure: exactly one row per position-set of {0,1,2} (family 1)
// and exactly one row per position-set of {4,5,6} (family 2).
// WLOG the identity is a row.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define N 7
#define NP 5040
#define W ((NP + 63) / 64)
#define NG 70

static int perm[NP][N];
static int g1[NP], g2[NP];
static uint64_t compat[NP][W];
static uint64_t gmask[NG][W];
static long long nodes = 0;
static int chosen[35];
static int depth_count[40];

static inline int popc(const uint64_t *s) { int c = 0; for (int w = 0; w < W; w++) c += __builtin_popcountll(s[w]); return c; }
static inline int meets3(const uint64_t *a, const uint64_t *b, const uint64_t *c) {
  for (int w = 0; w < W; w++) if (a[w] & b[w] & c[w]) return 1;
  return 0;
}

// Propagate to arc consistency. Returns 0 on wipeout.
static int propagate(uint64_t *dom, const unsigned char *used) {
  int changed = 1;
  while (changed) {
    changed = 0;
    for (int w = 0; w < W; w++) {
      uint64_t b = dom[w];
      while (b) {
        int p = w * 64 + __builtin_ctzll(b);
        b &= b - 1;
        for (int h = 0; h < NG; h++) {
          if (used[h] || h == g1[p] || h == g2[p]) continue;
          if (!meets3(dom, gmask[h], compat[p])) {
            dom[w] &= ~(1ULL << (p % 64));
            changed = 1;
            break;
          }
        }
      }
    }
    for (int h = 0; h < NG; h++) {
      if (used[h]) continue;
      int empty = 1;
      for (int w = 0; w < W; w++) if (dom[w] & gmask[h][w]) { empty = 0; break; }
      if (empty) return 0;
    }
  }
  return 1;
}

static int dfs(uint64_t *dom, int assigned, unsigned char *used) {
  nodes++;
  depth_count[assigned]++;
  if (assigned == 35) return 1;
  if (!propagate(dom, used)) return 0;
  int best = -1, bestc = 1 << 30;
  uint64_t tmp[W];
  for (int g = 0; g < NG; g++) {
    if (used[g]) continue;
    for (int w = 0; w < W; w++) tmp[w] = dom[w] & gmask[g][w];
    int c = popc(tmp);
    if (c < bestc) { bestc = c; best = g; }
  }
  uint64_t cand[W], nd[W];
  for (int w = 0; w < W; w++) cand[w] = dom[w] & gmask[best][w];
  for (int w = 0; w < W; w++) {
    uint64_t b = cand[w];
    while (b) {
      int p = w * 64 + __builtin_ctzll(b);
      b &= b - 1;
      for (int x = 0; x < W; x++) nd[x] = dom[x] & compat[p][x] & ~gmask[g1[p]][x] & ~gmask[g2[p]][x];
      used[g1[p]] = used[g2[p]] = 1;
      chosen[g1[p]] = p;
      int r = dfs(nd, assigned + 1, used);
      used[g1[p]] = used[g2[p]] = 0;
      if (r) return 1;
      if (assigned <= 2) {
        fprintf(stderr, "[%ld] depth %d: finished a branch, nodes=%lld\n", (long) time(NULL), assigned, nodes);
      }
    }
  }
  return 0;
}

int main(void) {
  int a[N]; for (int i = 0; i < N; i++) a[i] = i;
  int k = 0;
  for (;;) {
    memcpy(perm[k++], a, sizeof a);
    int i = N - 2; while (i >= 0 && a[i] > a[i + 1]) i--;
    if (i < 0) break;
    int j = N - 1; while (a[j] < a[i]) j--;
    int t = a[i]; a[i] = a[j]; a[j] = t;
    for (int l = i + 1, r = N - 1; l < r; l++, r--) { t = a[l]; a[l] = a[r]; a[r] = t; }
  }
  int c1[128], c2[128]; memset(c1, -1, sizeof c1); memset(c2, -1, sizeof c2);
  int n1 = 0, n2 = 0;
  for (int p = 0; p < NP; p++) {
    int x = 0, y = 0;
    for (int i = 0; i < N; i++) { if (perm[p][i] <= 2) x |= 1 << i; if (perm[p][i] >= 4) y |= 1 << i; }
    if (c1[x] < 0) c1[x] = n1++;
    if (c2[y] < 0) c2[y] = n2++;
    g1[p] = c1[x]; g2[p] = 35 + c2[y];
    gmask[g1[p]][p / 64] |= 1ULL << (p % 64);
    gmask[g2[p]][p / 64] |= 1ULL << (p % 64);
  }
  for (int p = 0; p < NP; p++) for (int q = 0; q < NP; q++) {
    int m = 0; for (int i = 0; i < N; i++) { int x = abs(perm[p][i] - perm[q][i]); if (x > m) m = x; }
    int ok = m >= 4;
    // Lemma: if A(q) = U(p) - x for some x in A(p), then q(x) = p(x) + 4 (and symmetrically).
    for (int dir = 0; dir < 2 && ok; dir++) {
      const int *P = dir ? perm[q] : perm[p], *Q = dir ? perm[p] : perm[q];
      int Ap = 0, Up = 0, Aq = 0;
      for (int i = 0; i < N; i++) { if (P[i] <= 2) Ap |= 1 << i; if (P[i] <= 3) Up |= 1 << i; if (Q[i] <= 2) Aq |= 1 << i; }
      if (Aq != Ap && (Aq & ~Up) == 0) {
        int x = __builtin_ctz(Up & ~Aq);
        if (!(Ap >> x & 1)) { fprintf(stderr, "lemma setup bug\n"); exit(1); }
        if (Q[x] != P[x] + 4) ok = 0;
      }
    }
    if (ok) compat[p][q / 64] |= 1ULL << (q % 64);
  }
  printf("families: %d + %d groups\n", n1, n2); fflush(stdout);
  unsigned char used[NG] = {0};
  uint64_t dom[W];
  int id = 0;
  for (int w = 0; w < W; w++) dom[w] = compat[id][w] & ~gmask[g1[id]][w] & ~gmask[g2[id]][w];
  used[g1[id]] = used[g2[id]] = 1; chosen[g1[id]] = id;
  int ok = dfs(dom, 1, used);
  printf("result: %s, nodes=%lld\n", ok ? "FOUND 35" : "NONE: no (7,4)-PA of size 35", nodes);
  for (int d = 0; d <= 35; d++) if (depth_count[d]) printf("depth %d: %d nodes\n", d, depth_count[d]);
  if (ok) for (int g = 0; g < 35; g++) { for (int i = 0; i < N; i++) printf("%d ", perm[chosen[g]][i]); printf("\n"); }
  return 0;
}
