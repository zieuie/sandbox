// Host driver for the GPU tabu search over symmetric template PAs.
// See README.md.  Needs only libc and the NVIDIA driver at run time.
//
// Usage: gpuclimb n d seconds [options]
//   --chains C        independent chains, one per thread block (default 56)
//   --steps K         steps per kernel launch (default 200)
//   --noise p         probability of a random swap (default 0.01)
//   --tenure lo span  tabu tenure lo + rand(0..span) steps (default 10 10)
//   --stall S         steps without improvement before a random kick (default 20000)
//   --seed X          RNG seed (default: time)
//   --threads t       threads per block (default 256)
//   --device i        CUDA device (default 0)
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <signal.h>
#include <unistd.h>
#include "kh_cuda.h"

static volatile sig_atomic_t stop_requested = 0;
static void on_signal(int sig) { (void) sig; stop_requested = 1; }

extern const kh_cuda_image_t chebyshev_images[];
extern const unsigned chebyshev_images_count;

#define ST 20
#define MAXS 192
#define MAXG 64

typedef struct { int n, d, R, NG, C, steps; float noise; int tenure_lo, tenure_span, stall; float p_replace; } Params;

static int n, d, NG, R;
static int gt[MAXG], gp[MAXG][ST];
static uint64_t rs;
static uint64_t xr(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }

static void act(int h, const int8_t *r, int8_t *out) { for (int i = 0; i < n; i++) out[gp[h][i]] = gt[h] ? (int8_t) (n - 1 - r[i]) : r[i]; }
static int act_mask(int h, int m) { int o = 0; for (int i = 0; i < n; i++) if (m >> i & 1) o |= 1 << gp[h][i]; if (gt[h]) o = ((1 << n) - 1) & ~o; return o; }
static int nsep(const int8_t *a, const int8_t *b) { int c = 0; for (int i = 0; i < n; i++) { int x = a[i] - b[i]; c += (x >= d || -x >= d); } return c; }
static void random_fill(int mask, int8_t *r) {
  int lo[ST], hi[ST], nl = 0, nh = 0;
  for (int v = 0; v < n; v++) { if (v < d) lo[nl++] = v; else hi[nh++] = v; }
  for (int i = nl - 1; i > 0; i--) { int j = xr() % (i + 1); int t = lo[i]; lo[i] = lo[j]; lo[j] = t; }
  for (int i = nh - 1; i > 0; i--) { int j = xr() % (i + 1); int t = hi[i]; hi[i] = hi[j]; hi[j] = t; }
  int a = 0, b = 0;
  for (int i = 0; i < n; i++) r[i] = (int8_t) ((mask >> i & 1) ? lo[a++] : hi[b++]);
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

#define CK(x) do { int _e = (x); if (_e) { fprintf(stderr, "%s:%d CUDA error %d (%s)\n", __FILE__, __LINE__, _e, kh_cuda_error(&cu, _e)); exit(1); } } while (0)

// Expand reps to the full array and check every pair. Returns min distance.
static int verify(const int8_t *reps, int8_t *full) {
  for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, reps + j * ST, full + ((size_t) j * NG + h) * ST);
  int rows = R * NG, mind = 99;
  for (int a = 0; a < rows; a++) {
    // permutation check
    int seen = 0; for (int i = 0; i < n; i++) seen |= 1 << full[(size_t) a * ST + i];
    if (seen != (1 << n) - 1) return -1;
    for (int b = 0; b < a; b++) {
      int m = 0; const int8_t *x = full + (size_t) a * ST, *y = full + (size_t) b * ST;
      for (int i = 0; i < n; i++) { int z = x[i] - y[i]; if (z < 0) z = -z; if (z > m) m = z; }
      if (m < mind) mind = m;
      if (mind < d) return mind;
    }
  }
  return mind;
}

static void write_rows(const char *fn, const int8_t *full) {
  FILE *f = fopen(fn, "w");
  for (int a = 0; a < R * NG; a++) { for (int i = 0; i < n; i++) fprintf(f, "%d ", full[(size_t) a * ST + i]); fprintf(f, "\n"); }
  fclose(f);
}

// Orbit trim: build the conflict graph on representatives (an edge j-k if some
// row of orbit j conflicts with some row of orbit k; reps that conflict with
// their own orbit are dropped outright), find a minimum vertex cover by branch
// and bound, and keep every row of the remaining orbits.
static int ou[1 << 16], ov[1 << 16], one, obest, *obestset, oR;
static void mvc(int *removed, int nrem) {
  if (nrem >= obest) return;
  int e = -1;
  for (int i = 0; i < one; i++) if (!removed[ou[i]] && !removed[ov[i]]) { e = i; break; }
  if (e < 0) { obest = nrem; memcpy(obestset, removed, sizeof(int) * oR); return; }
  // lower bound: greedy matching size among uncovered edges
  static char used[1 << 16]; int lb = 0;
  memset(used, 0, oR);
  for (int i = 0; i < one; i++) { int a = ou[i], b = ov[i]; if (removed[a] || removed[b] || used[a] || used[b]) continue; used[a] = used[b] = 1; lb++; }
  if (nrem + lb >= obest) return;
  removed[ou[e]] = 1; mvc(removed, nrem + 1); removed[ou[e]] = 0;
  removed[ov[e]] = 1; mvc(removed, nrem + 1); removed[ov[e]] = 0;
}
static int orbit_trim(const int8_t *reps, const int8_t *full, char *keep) {
  oR = R; one = 0;
  int *removed = calloc(R, sizeof(int)); obestset = calloc(R, sizeof(int));
  int forced = 0;
  for (int j = 0; j < R; j++) for (int h = 1; h < NG; h++) if (!nsep(reps + j * ST, full + ((size_t) j * NG + h) * ST)) { if (!removed[j]) forced++; removed[j] = 1; break; }
  for (int j = 0; j < R; j++) for (int k = 0; k < j; k++) {
    if (removed[j] || removed[k]) continue;
    for (int h = 0; h < NG; h++) if (!nsep(reps + j * ST, full + ((size_t) k * NG + h) * ST)) { if (one < (1 << 16)) { ou[one] = j; ov[one] = k; one++; } break; }
  }
  // greedy upper bound first, then exact search (bounded effort via obest)
  obest = R + 1;
  { int *g = calloc(R, sizeof(int)); memcpy(g, removed, sizeof(int) * R); int cnt = 0;
    for (;;) { int *deg = calloc(R, sizeof(int)); int any = 0;
      for (int i = 0; i < one; i++) if (!g[ou[i]] && !g[ov[i]]) { deg[ou[i]]++; deg[ov[i]]++; any = 1; }
      if (!any) { free(deg); break; }
      int w = 0; for (int j = 1; j < R; j++) if (deg[j] > deg[w]) w = j; g[w] = 1; cnt++; free(deg); }
    obest = cnt + 1; memcpy(obestset, g, sizeof(int) * R); free(g);
    obest = cnt; }
  if (one <= 400) mvc(removed, 0);
  int keptR = 0;
  for (int j = 0; j < R; j++) { int k = !(removed[j] || obestset[j]); keptR += k; for (int h = 0; h < NG; h++) keep[(size_t) j * NG + h] = (char) k; }
  (void) forced;
  free(removed); free(obestset);
  return keptR * NG;
}

// Greedy trim: drop max-degree rows of the conflict graph until none conflict.
// Returns the number of rows kept; keep[] marks them.
static int trim(const int8_t *full, int rows, char *keep) {
  int *deg = calloc(rows, sizeof(int));
  size_t cap = 1024, ne = 0; int *eu = malloc(sizeof(int) * cap), *ev = malloc(sizeof(int) * cap);
  for (int a = 0; a < rows; a++) for (int b = 0; b < a; b++) {
    if (nsep(full + (size_t) a * ST, full + (size_t) b * ST)) continue;
    if (ne == cap) { cap *= 2; eu = realloc(eu, sizeof(int) * cap); ev = realloc(ev, sizeof(int) * cap); }
    eu[ne] = a; ev[ne] = b; ne++; deg[a]++; deg[b]++;
  }
  for (int a = 0; a < rows; a++) keep[a] = 1;
  int kept = rows;
  for (;;) {
    int w = -1; for (int a = 0; a < rows; a++) if (keep[a] && deg[a] > 0 && (w < 0 || deg[a] > deg[w])) w = a;
    if (w < 0) break;
    keep[w] = 0; kept--;
    for (size_t e = 0; e < ne; e++) {
      if (eu[e] == w && keep[ev[e]]) deg[ev[e]]--;
      else if (ev[e] == w && keep[eu[e]]) deg[eu[e]]--;
    }
    deg[w] = 0;
  }
  free(deg); free(eu); free(ev);
  return kept;
}

// Write the kept rows, after checking every kept pair. Returns min distance (99 if < 2 rows).
static int write_trimmed(const int8_t *full, int rows, const char *keep, const char *fn) {
  int mind = 99;
  for (int a = 0; a < rows; a++) if (keep[a]) for (int b = 0; b < a; b++) if (keep[b]) {
    int m = 0; const int8_t *x = full + (size_t) a * ST, *y = full + (size_t) b * ST;
    for (int i = 0; i < n; i++) { int z = x[i] - y[i]; if (z < 0) z = -z; if (z > m) m = z; }
    if (m < mind) mind = m;
  }
  if (mind >= d) {
    FILE *f = fopen(fn, "w");
    for (int a = 0; a < rows; a++) if (keep[a]) { for (int i = 0; i < n; i++) fprintf(f, "%d ", full[(size_t) a * ST + i]); fprintf(f, "\n"); }
    fclose(f);
  }
  return mind;
}

// Copy the winner's best reps restricted to its active orbits into out; returns count.
static int compact_best(const int8_t *all, const uint8_t *act, int Rall, int8_t *out) {
  int k = 0;
  for (int j = 0; j < Rall; j++) if (act[j]) memcpy(out + (size_t) k++ * ST, all + (size_t) j * ST, ST);
  return k;
}

static void write_state(const char *path, int nactive, int C, const int8_t *reps, const uint8_t *acts, double elapsed, long long steps) {
  char tmp[512]; snprintf(tmp, sizeof tmp, "%s.tmp", path);
  FILE *f = fopen(tmp, "wb"); if (!f) return;
  int hdr[6] = { n, d, R, NG, nactive, C };
  fwrite("CHPA1", 1, 6, f); fwrite(hdr, sizeof(int), 6, f); fwrite(&elapsed, sizeof elapsed, 1, f); fwrite(&steps, sizeof steps, 1, f);
  fwrite(reps, 1, (size_t) C * R * ST, f); fwrite(acts, 1, (size_t) C * R, f);
  fclose(f); rename(tmp, path);
}

static void write_status(const char *path, const char *state, int target_rows, int best, int mean, double elapsed, long long steps, int trim) {
  char tmp[512], host[64] = "?"; snprintf(tmp, sizeof tmp, "%s.tmp", path); gethostname(host, sizeof host - 1);
  FILE *f = fopen(tmp, "w"); if (!f) return;
  fprintf(f, "{\"host\":\"%s\",\"n\":%d,\"d\":%d,\"target_rows\":%d,\"state\":\"%s\",\"best_conflicts\":%d,\"mean_conflicts\":%d,"
             "\"elapsed_s\":%.0f,\"steps_per_chain\":%lld,\"best_trim_rows\":%d,\"updated\":%ld}\n",
          host, n, d, target_rows, state, best, mean, elapsed, steps, trim, (long) time(NULL));
  fclose(f); rename(tmp, path);
}

int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: %s n d seconds [--chains C] [--steps K] [--noise p] [--tenure lo span] [--stall S] [--seed X] [--threads t] [--device i]\n", argv[0]); return 1; }
  n = atoi(argv[1]); d = atoi(argv[2]); double secs = atof(argv[3]);
  int C = 56, steps = 200, tlo = 10, tspan = 10, stall = 20000, threads = 256, device = 0;
  int nactive = 0; float preplace = 0.05f;
  const char *state_path = NULL, *status_path = "status.json"; double save_every = 300;
  double ckpt_secs = 600; const char *group_opt = "auto", *resume = NULL;
  float noise = 0.01f; uint64_t seed = (uint64_t) time(NULL);
  for (int a = 4; a < argc; a++) {
    if (!strcmp(argv[a], "--chains")) C = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--steps")) steps = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--noise")) noise = (float) atof(argv[++a]);
    else if (!strcmp(argv[a], "--tenure")) { tlo = atoi(argv[++a]); tspan = atoi(argv[++a]); }
    else if (!strcmp(argv[a], "--stall")) stall = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--seed")) seed = strtoull(argv[++a], 0, 10);
    else if (!strcmp(argv[a], "--threads")) threads = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--device")) device = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--ckpt")) ckpt_secs = atof(argv[++a]);
    else if (!strcmp(argv[a], "--group")) group_opt = argv[++a];
    else if (!strcmp(argv[a], "--active")) nactive = atoi(argv[++a]);
    else if (!strcmp(argv[a], "--preplace")) preplace = (float) atof(argv[++a]);
    else if (!strcmp(argv[a], "--resume")) resume = argv[++a];
    else if (!strcmp(argv[a], "--state")) state_path = argv[++a];
    else if (!strcmp(argv[a], "--save-every")) save_every = atof(argv[++a]);
    else if (!strcmp(argv[a], "--status")) status_path = argv[++a];
    else { fprintf(stderr, "unknown option %s\n", argv[a]); return 1; }
  }
  rs = seed * 6364136223846793005ULL + 1442695040888963407ULL; if (!rs) rs = 1;
  if (n > ST || n > 20) { fprintf(stderr, "n too large\n"); return 1; }
  // group: n = 2d -> x -> +-x+b mod (n-1) (with value reversal for "-");
  // otherwise the first of Z_n, Z_(n-1) (last position fixed), trivial that acts freely.
  if (n > 2 * d) { fprintf(stderr, "need n <= 2d\n"); return 1; }
  char *seen = calloc(1u << n, 1); int *masks = malloc(sizeof(int) * (1u << n));
  int first = (n == 2 * d ? 0 : 1);
  if (!strcmp(group_opt, "trivial")) first = 3;
  for (int attempt = first; attempt <= 3; attempt++) {
    NG = 0;
    if (attempt == 0) { int m = n - 1; for (int s = 0; s < 2; s++) for (int b = 0; b < m; b++) { gt[NG] = s; for (int x = 0; x < m; x++) gp[NG][x] = (((s ? -x : x) + b) % m + m) % m; gp[NG][m] = m; NG++; } }
    else if (attempt == 1) { for (int b = 0; b < n; b++) { gt[NG] = 0; for (int x = 0; x < n; x++) gp[NG][x] = (x + b) % n; NG++; } }
    else if (attempt == 2) { int m = n - 1; for (int b = 0; b < m; b++) { gt[NG] = 0; for (int x = 0; x < m; x++) gp[NG][x] = (x + b) % m; gp[NG][m] = m; NG++; } }
    else { gt[0] = 0; for (int x = 0; x < n; x++) gp[0][x] = x; NG = 1; }
    if (NG > MAXG) continue;
    memset(seen, 0, 1u << n);
    R = 0; int ok = 1;
    for (int m = 0; m < (1 << n) && ok; m++) {
      if (__builtin_popcount(m) != d || seen[m]) continue;
      int os = 0; for (int h = 0; h < NG; h++) { int m2 = act_mask(h, m); if (!seen[m2]) { seen[m2] = 1; os++; } }
      if (os != NG) ok = 0;
      masks[R++] = m;
    }
    if (ok) { printf("group: %s\n", attempt == 0 ? "dihedral+reversal" : attempt == 1 ? "Z_n" : attempt == 2 ? "Z_(n-1)" : "trivial"); break; }
  }
  int rows = R * NG;
  printf("P(%d,%d): |G|=%d reps=%d rows=%d chains=%d\n", n, d, NG, R, rows, C); fflush(stdout);

  // initial states (host)
  size_t repB = (size_t) C * R * ST, imgB = (size_t) C * rows * ST;
  int8_t *rep = calloc(repB, 1), *img = calloc(imgB, 1);
  int *conf = calloc((size_t) C * R, sizeof(int)), *T = calloc(C, sizeof(int)), *best = calloc(C, sizeof(int));
  if (nactive <= 0 || nactive > R) nactive = R;
  if (nactive < R) printf("subset mode: %d of %d orbits active (%d rows)\n", nactive, R, nactive * NG);
  uint8_t *active = calloc((size_t) C * R, 1);
  uint64_t *rng = malloc(sizeof(uint64_t) * C);
  // saved state (all chains' best arrays) from an earlier run with the same parameters
  char state_default[96];
  if (!state_path) { sprintf(state_default, "state_%d_%d_%d.bin", n, d, nactive * NG); state_path = state_default; }
  int st_ok = 0, st_C = 0; double prev_elapsed = 0; long long prev_steps = 0;
  int8_t *st_rep = NULL; uint8_t *st_act = NULL;
  { FILE *f = fopen(state_path, "rb");
    if (f) {
      char magic[6] = {0}; int hdr[6]; double pe; long long ps;
      if (fread(magic, 1, 6, f) == 6 && !memcmp(magic, "CHPA1", 5) && fread(hdr, sizeof(int), 6, f) == 6 &&
          fread(&pe, sizeof pe, 1, f) == 1 && fread(&ps, sizeof ps, 1, f) == 1 &&
          hdr[0] == n && hdr[1] == d && hdr[2] == R && hdr[3] == NG && hdr[4] == nactive && hdr[5] > 0) {
        st_C = hdr[5];
        st_rep = malloc((size_t) st_C * R * ST); st_act = malloc((size_t) st_C * R);
        if (fread(st_rep, 1, (size_t) st_C * R * ST, f) == (size_t) st_C * R * ST && fread(st_act, 1, (size_t) st_C * R, f) == (size_t) st_C * R) {
          st_ok = 1; prev_elapsed = pe; prev_steps = ps;
          printf("resuming %d chains from %s (%.0f s and %lld steps per chain so far)\n", st_C, state_path, pe, ps);
        }
      } else printf("ignoring %s (different parameters)\n", state_path);
      fclose(f);
    }
  }
  for (int c = 0; c < C; c++) {
    int8_t *rp = rep + (size_t) c * R * ST, *ip = img + (size_t) c * rows * ST;
    for (int j = 0; j < R; j++) random_fill(masks[j], rp + j * ST);
    if (resume) {
      static int *rep_of = NULL;
      if (!rep_of) { rep_of = malloc(sizeof(int) * (1u << n)); for (int m = 0; m < (1 << n); m++) rep_of[m] = -1; for (int j = 0; j < R; j++) rep_of[masks[j]] = j; }
      FILE *f = fopen(resume, "r"); if (!f) { perror(resume); return 1; }
      int8_t row[ST]; int got = 0;
      for (;;) {
        int ok = 1;
        for (int i = 0; i < n; i++) { int v; if (fscanf(f, "%d", &v) != 1) { ok = 0; break; } row[i] = (int8_t) v; }
        if (!ok) break;
        int m = 0; for (int i = 0; i < n; i++) if (row[i] < d) m |= 1 << i;
        if (rep_of[m] >= 0) { memcpy(rp + rep_of[m] * ST, row, n); got++; }
      }
      fclose(f);
      if (c == 0) printf("resumed %d representative rows from %s\n", got, resume);
    }
    for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, rp + j * ST, ip + ((size_t) j * NG + h) * ST);
    uint8_t *ac = active + (size_t) c * R;
    { int *perm = malloc(sizeof(int) * R); for (int j = 0; j < R; j++) perm[j] = j;
      for (int j = R - 1; j > 0; j--) { int k = xr() % (j + 1); int t = perm[j]; perm[j] = perm[k]; perm[k] = t; }
      for (int q = 0; q < nactive; q++) { ac[perm[q]] = 1; }
      free(perm); }
    if (st_ok) {
      int sc_ = c % st_C;
      memcpy(rp, st_rep + (size_t) sc_ * R * ST, (size_t) R * ST);
      memcpy(ac, st_act + (size_t) sc_ * R, R);
      for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, rp + j * ST, ip + ((size_t) j * NG + h) * ST);
    }
    int tot = 0;
    for (int j = 0; j < R; j++) {
      if (!ac[j]) continue;
      int cnt = 0;
      for (int y = 0; y < rows; y++) { if (y == j * NG || !ac[y / NG]) continue; if (!nsep(rp + j * ST, ip + (size_t) y * ST)) cnt++; }
      conf[(size_t) c * R + j] = cnt; tot += cnt;
    }
    T[c] = tot; best[c] = tot; rng[c] = xr() | 1;
  }

  kh_cuda_t cu = {0}; const char *err = NULL;
  if (!kh_cuda_open(&cu, device, chebyshev_images, chebyshev_images_count, &err)) { fprintf(stderr, "cuda: %s\n", err); return 1; }
  printf("device: %s (sm_%u, %.1f GB)\n", cu.name, cu.arch, cu.total_bytes / 1e9); fflush(stdout);
  void *fn = kh_cuda_function(&cu, "tabu_steps");
  if (!fn) { fprintf(stderr, "kernel not found\n"); return 1; }
  kh_dptr_t d_gt, d_gp, d_rep, d_img, d_conf, d_T, d_best, d_brep, d_sc, d_tabu, d_rng, d_iter, d_li, d_moves, d_act, d_bact;
  int hgp[MAXG * ST] = {0}; for (int h = 0; h < NG; h++) for (int i = 0; i < n; i++) hgp[h * ST + i] = gp[h][i];
  CK(cu.cuMemAlloc(&d_gt, sizeof(int) * MAXG)); CK(cu.cuMemcpyHtoD(d_gt, gt, sizeof(int) * MAXG));
  CK(cu.cuMemAlloc(&d_gp, sizeof(hgp))); CK(cu.cuMemcpyHtoD(d_gp, hgp, sizeof(hgp)));
  CK(cu.cuMemAlloc(&d_rep, repB)); CK(cu.cuMemcpyHtoD(d_rep, rep, repB));
  CK(cu.cuMemAlloc(&d_brep, repB)); CK(cu.cuMemcpyHtoD(d_brep, rep, repB));
  CK(cu.cuMemAlloc(&d_img, imgB)); CK(cu.cuMemcpyHtoD(d_img, img, imgB));
  CK(cu.cuMemAlloc(&d_conf, sizeof(int) * C * R)); CK(cu.cuMemcpyHtoD(d_conf, conf, sizeof(int) * C * R));
  CK(cu.cuMemAlloc(&d_T, sizeof(int) * C)); CK(cu.cuMemcpyHtoD(d_T, T, sizeof(int) * C));
  CK(cu.cuMemAlloc(&d_best, sizeof(int) * C)); CK(cu.cuMemcpyHtoD(d_best, best, sizeof(int) * C));
  CK(cu.cuMemAlloc(&d_sc, (size_t) C * rows));
  CK(cu.cuMemAlloc(&d_tabu, sizeof(int) * (size_t) C * R * MAXS)); CK(cu.cuMemsetD32(d_tabu, 0, (size_t) C * R * MAXS));
  CK(cu.cuMemAlloc(&d_rng, sizeof(uint64_t) * C)); CK(cu.cuMemcpyHtoD(d_rng, rng, sizeof(uint64_t) * C));
  CK(cu.cuMemAlloc(&d_iter, sizeof(int) * C)); CK(cu.cuMemsetD32(d_iter, 0, C));
  CK(cu.cuMemAlloc(&d_li, sizeof(int) * C)); CK(cu.cuMemsetD32(d_li, 0, C));
  CK(cu.cuMemAlloc(&d_moves, sizeof(int) * C)); CK(cu.cuMemsetD32(d_moves, 0, C));
  CK(cu.cuMemAlloc(&d_act, (size_t) C * R)); CK(cu.cuMemcpyHtoD(d_act, active, (size_t) C * R));
  CK(cu.cuMemAlloc(&d_bact, (size_t) C * R)); CK(cu.cuMemcpyHtoD(d_bact, active, (size_t) C * R));

  Params P = { n, d, R, NG, C, steps, noise, tlo, tspan, stall, nactive < R ? preplace : 0.0f };
  void *args[] = { &P, &d_gt, &d_gp, &d_rep, &d_img, &d_conf, &d_T, &d_best, &d_brep, &d_sc, &d_tabu, &d_rng, &d_iter, &d_li, &d_moves, &d_act, &d_bact };
  double t0 = now(), last_print = t0, last_ckpt = t0;
  int8_t *full = malloc((size_t) rows * ST);
  int8_t *brep = malloc((size_t) R * ST), *ball = malloc((size_t) R * ST);
  uint8_t *bact = malloc(R); int Rall = R;
  long long launches = 0;
  int overall = 1 << 30, winner = -1, best_trim = 0;
  double ckpt_every = ckpt_secs;
  signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
  double last_save = t0, last_status = 0;
  int8_t *all_brep = malloc(repB); uint8_t *all_bact = malloc((size_t) C * R);
  #define SAVE_STATE() do { CK(cu.cuMemcpyDtoH(all_brep, d_brep, repB)); CK(cu.cuMemcpyDtoH(all_bact, d_bact, (size_t) C * R)); \
      write_state(state_path, nactive, C, all_brep, all_bact, prev_elapsed + (now() - t0), prev_steps + launches * steps); } while (0)
  while (now() - t0 < secs && !stop_requested) {
    CK(kh_cuda_launch(&cu, fn, C, threads, args));
    CK(cu.cuCtxSynchronize());
    launches++;
    CK(cu.cuMemcpyDtoH(best, d_best, sizeof(int) * C));
    int bc = 0; for (int c = 1; c < C; c++) if (best[c] < best[bc]) bc = c;
    if (best[bc] < overall) { overall = best[bc]; winner = bc; }
    if (overall == 0) break;
    double t = now();
    if (t - last_save > save_every) { last_save = t; SAVE_STATE(); }
    if (t - last_status > 30) {
      last_status = t;
      CK(cu.cuMemcpyDtoH(T, d_T, sizeof(int) * C));
      long long sT = 0; for (int c = 0; c < C; c++) sT += T[c];
      write_status(status_path, "running", nactive * NG, overall, (int) (sT / C), prev_elapsed + (t - t0), prev_steps + launches * steps, best_trim);
    }
    if (t - last_print > 30) {
      last_print = t;
      CK(cu.cuMemcpyDtoH(T, d_T, sizeof(int) * C));
      long long sumT = 0; for (int c = 0; c < C; c++) sumT += T[c];
      printf("[%.0fs] steps/chain=%lld best=%d (chain %d) mean current=%.1f\n", t - t0, launches * steps, overall, winner, (double) sumT / C);
      fflush(stdout);
    }
    if (t - last_ckpt > ckpt_every) {
      last_ckpt = t;
      CK(cu.cuMemcpyDtoH(ball, d_brep + (size_t) winner * Rall * ST, (size_t) Rall * ST));
      CK(cu.cuMemcpyDtoH(bact, d_bact + (size_t) winner * Rall, (size_t) Rall));
      R = compact_best(ball, bact, Rall, brep); rows = R * NG;
      for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, brep + j * ST, full + ((size_t) j * NG + h) * ST);
      char fnm[64]; sprintf(fnm, "pa_%d_%d_gpu_ckpt.txt", n, d); write_rows(fnm, full);
      char *keep = malloc(rows); int kept = trim(full, rows, keep);
      if (NG > 1) { char *k2 = malloc(rows); int kept2 = orbit_trim(brep, full, k2); if (kept2 > kept) { kept = kept2; memcpy(keep, k2, rows); } free(k2); }
      if (kept > best_trim) {
        sprintf(fnm, "pa_%d_%d_trim_%d.txt", n, d, kept);
        int md = write_trimmed(full, rows, keep, fnm);
        printf("[%.0fs] trimmed best array to %d rows (min distance %d) -> %s\n", t - t0, kept, md, md >= d ? fnm : "NOT WRITTEN");
        if (md >= d) best_trim = kept;
        fflush(stdout);
      }
      free(keep);
      R = Rall; rows = R * NG;
    }
  }
  double el = now() - t0;
  SAVE_STATE();
  write_status(status_path, overall == 0 ? "solved" : stop_requested ? "stopped" : "timeout", nactive * NG, overall, -1, prev_elapsed + el, prev_steps + launches * steps, best_trim);
  if (stop_requested) printf("stopped by signal; state saved to %s\n", state_path);
  printf("finished: best=%d after %.0fs, %lld steps per chain (%.0f steps/s total)\n", overall, el, launches * steps, launches * steps * (double) C / el);
  CK(cu.cuMemcpyDtoH(ball, d_brep + (size_t) winner * Rall * ST, (size_t) Rall * ST));
  CK(cu.cuMemcpyDtoH(bact, d_bact + (size_t) winner * Rall, (size_t) Rall));
  R = compact_best(ball, bact, Rall, brep); rows = R * NG;
  int md = verify(brep, full);
  char fnm[64];
  if (overall == 0) {
    printf("verification: min distance %d over %d rows -> %s\n", md, rows, md >= d ? "VALID" : "INVALID");
    if (md >= d) sprintf(fnm, "pa_%d_%d_gpu_%d.txt", n, d, rows); else sprintf(fnm, "pa_%d_%d_gpu_bad.txt", n, d);
  } else sprintf(fnm, "pa_%d_%d_gpu_best.txt", n, d);
  write_rows(fnm, full);
  printf("wrote %s\n", fnm);
  if (overall > 0 && !stop_requested) {
    char *keep = malloc(rows); int kept = trim(full, rows, keep);
    if (NG > 1) { char *k2 = malloc(rows); int kept2 = orbit_trim(brep, full, k2); if (kept2 > kept) { kept = kept2; memcpy(keep, k2, rows); } free(k2); }
    sprintf(fnm, "pa_%d_%d_trim_%d.txt", n, d, kept);
    int mdt = write_trimmed(full, rows, keep, fnm);
    printf("final trim: %d of %d rows, min distance %d -> %s\n", kept, rows, mdt, mdt >= d ? fnm : "NOT WRITTEN");
  }
  kh_cuda_close(&cu);
  return 0;
}
