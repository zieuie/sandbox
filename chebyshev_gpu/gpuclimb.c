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
#include "kh_cuda.h"

extern const kh_cuda_image_t chebyshev_images[];
extern const unsigned chebyshev_images_count;

#define ST 20
#define MAXS 192
#define MAXG 64

typedef struct { int n, d, R, NG, C, steps; float noise; int tenure_lo, tenure_span, stall; } Params;

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

int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: %s n d seconds [--chains C] [--steps K] [--noise p] [--tenure lo span] [--stall S] [--seed X] [--threads t] [--device i]\n", argv[0]); return 1; }
  n = atoi(argv[1]); d = atoi(argv[2]); double secs = atof(argv[3]);
  int C = 56, steps = 200, tlo = 10, tspan = 10, stall = 20000, threads = 256, device = 0;
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
    else { fprintf(stderr, "unknown option %s\n", argv[a]); return 1; }
  }
  rs = seed * 6364136223846793005ULL + 1442695040888963407ULL; if (!rs) rs = 1;
  if (n > ST || n > 20) { fprintf(stderr, "n too large\n"); return 1; }
  // group (same as cpu/symclimb3.c)
  NG = 0;
  if (n == 2 * d) { int m = n - 1; for (int s = 0; s < 2; s++) for (int b = 0; b < m; b++) { gt[NG] = s; for (int x = 0; x < m; x++) gp[NG][x] = (((s ? -x : x) + b) % m + m) % m; gp[NG][m] = m; NG++; } }
  else if (n == 2 * d - 1) { for (int b = 0; b < n; b++) { gt[NG] = 0; for (int x = 0; x < n; x++) gp[NG][x] = (x + b) % n; NG++; } }
  else { fprintf(stderr, "need n = 2d or 2d-1\n"); return 1; }
  char *seen = calloc(1u << n, 1); int *masks = malloc(sizeof(int) * (1u << n));
  R = 0;
  for (int m = 0; m < (1 << n); m++) {
    if (__builtin_popcount(m) != d || seen[m]) continue;
    int os = 0; for (int h = 0; h < NG; h++) { int m2 = act_mask(h, m); if (!seen[m2]) { seen[m2] = 1; os++; } }
    if (os != NG) { fprintf(stderr, "group not free\n"); return 1; }
    masks[R++] = m;
  }
  int rows = R * NG;
  printf("P(%d,%d): |G|=%d reps=%d rows=%d chains=%d\n", n, d, NG, R, rows, C); fflush(stdout);

  // initial states (host)
  size_t repB = (size_t) C * R * ST, imgB = (size_t) C * rows * ST;
  int8_t *rep = calloc(repB, 1), *img = calloc(imgB, 1);
  int *conf = calloc((size_t) C * R, sizeof(int)), *T = calloc(C, sizeof(int)), *best = calloc(C, sizeof(int));
  uint64_t *rng = malloc(sizeof(uint64_t) * C);
  for (int c = 0; c < C; c++) {
    int8_t *rp = rep + (size_t) c * R * ST, *ip = img + (size_t) c * rows * ST;
    for (int j = 0; j < R; j++) random_fill(masks[j], rp + j * ST);
    for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, rp + j * ST, ip + ((size_t) j * NG + h) * ST);
    int tot = 0;
    for (int j = 0; j < R; j++) {
      int cnt = 0;
      for (int y = 0; y < rows; y++) { if (y == j * NG) continue; if (!nsep(rp + j * ST, ip + (size_t) y * ST)) cnt++; }
      conf[(size_t) c * R + j] = cnt; tot += cnt;
    }
    T[c] = tot; best[c] = tot; rng[c] = xr() | 1;
  }

  kh_cuda_t cu = {0}; const char *err = NULL;
  if (!kh_cuda_open(&cu, device, chebyshev_images, chebyshev_images_count, &err)) { fprintf(stderr, "cuda: %s\n", err); return 1; }
  printf("device: %s (sm_%u, %.1f GB)\n", cu.name, cu.arch, cu.total_bytes / 1e9); fflush(stdout);
  void *fn = kh_cuda_function(&cu, "tabu_steps");
  if (!fn) { fprintf(stderr, "kernel not found\n"); return 1; }
  kh_dptr_t d_gt, d_gp, d_rep, d_img, d_conf, d_T, d_best, d_brep, d_sc, d_tabu, d_rng, d_iter, d_li, d_moves;
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

  Params P = { n, d, R, NG, C, steps, noise, tlo, tspan, stall };
  void *args[] = { &P, &d_gt, &d_gp, &d_rep, &d_img, &d_conf, &d_T, &d_best, &d_brep, &d_sc, &d_tabu, &d_rng, &d_iter, &d_li, &d_moves };
  double t0 = now(), last_print = t0, last_ckpt = t0;
  int8_t *full = malloc((size_t) rows * ST);
  int8_t *brep = malloc((size_t) R * ST);
  long long launches = 0;
  int overall = 1 << 30, winner = -1;
  while (now() - t0 < secs) {
    CK(kh_cuda_launch(&cu, fn, C, threads, args));
    CK(cu.cuCtxSynchronize());
    launches++;
    CK(cu.cuMemcpyDtoH(best, d_best, sizeof(int) * C));
    int bc = 0; for (int c = 1; c < C; c++) if (best[c] < best[bc]) bc = c;
    if (best[bc] < overall) { overall = best[bc]; winner = bc; }
    if (overall == 0) break;
    double t = now();
    if (t - last_print > 30) {
      last_print = t;
      CK(cu.cuMemcpyDtoH(T, d_T, sizeof(int) * C));
      long long sumT = 0; for (int c = 0; c < C; c++) sumT += T[c];
      printf("[%.0fs] steps/chain=%lld best=%d (chain %d) mean current=%.1f\n", t - t0, launches * steps, overall, winner, (double) sumT / C);
      fflush(stdout);
    }
    if (t - last_ckpt > 600) {
      last_ckpt = t;
      CK(cu.cuMemcpyDtoH(brep, d_brep + (size_t) winner * R * ST, (size_t) R * ST));
      for (int j = 0; j < R; j++) for (int h = 0; h < NG; h++) act(h, brep + j * ST, full + ((size_t) j * NG + h) * ST);
      char fnm[64]; sprintf(fnm, "pa_%d_%d_gpu_ckpt.txt", n, d); write_rows(fnm, full);
    }
  }
  double el = now() - t0;
  printf("finished: best=%d after %.0fs, %lld steps per chain (%.0f steps/s total)\n", overall, el, launches * steps, launches * steps * (double) C / el);
  CK(cu.cuMemcpyDtoH(brep, d_brep + (size_t) winner * R * ST, (size_t) R * ST));
  int md = verify(brep, full);
  char fnm[64];
  if (overall == 0) {
    printf("verification: min distance %d over %d rows -> %s\n", md, rows, md >= d ? "VALID" : "INVALID");
    sprintf(fnm, md >= d ? "pa_%d_%d_gpu.txt" : "pa_%d_%d_gpu_bad.txt", n, d);
  } else sprintf(fnm, "pa_%d_%d_gpu_best.txt", n, d);
  write_rows(fnm, full);
  printf("wrote %s\n", fnm);
  kh_cuda_close(&cu);
  return 0;
}
