// GPU tabu search for symmetric template PAs (see README.md).
//
// One thread block = one independent chain.  A chain holds one representative
// row per template orbit; the full array is the orbit of those rows under a
// group G (images are stored).  A step picks a conflicted representative j,
// scores every swap of two positions inside one block of that row against all
// R*NG rows at once, and applies the best non-tabu swap (with a little noise).
//
// Score T = sum over reps j of conf[j], where conf[j] counts rows (k,h) != (j,e)
// that are not separated from rep j.  Pairs between different orbits are seen
// from both sides, so T counts them twice; T == 0 iff the array is a PA.

#define ST 20          // row stride in bytes (n <= 20)
#define MAXS 192       // max swaps per row
#define MAXG 64

typedef unsigned char u8;
typedef signed char i8;
typedef unsigned long long u64;

struct Params {
  int n, d, R, NG, C;
  int steps;           // steps per launch
  float noise;         // probability of a random swap instead of the best
  int tenure_lo, tenure_span;
  int stall;           // steps without improvement before a kick
  float p_replace;     // probability of trying an orbit replacement (subset mode)
};

__device__ __forceinline__ int far_(int x, int y, int d) { int z = x - y; return z >= d || -z >= d; }
__device__ __forceinline__ u64 rnd(u64 *s) { u64 x = *s; x ^= x << 13; x ^= x >> 7; x ^= x << 17; *s = x; return x; }

extern "C" __global__ void tabu_steps(
    Params P,
    const int *gt, const int *gp,        // group: gt[NG], gp[NG*ST]
    i8 *rep, i8 *img, int *conf, int *T, int *best, i8 *best_rep,
    u8 *sc, int *tabu, u64 *rng, int *iter, int *last_imp, int *moves_done,
    u8 *active, u8 *best_active)
{
  const int c = blockIdx.x, tid = threadIdx.x, nt = blockDim.x;
  const int n = P.n, d = P.d, R = P.R, NG = P.NG, rows = R * NG;
  i8 *rp = rep + (size_t) c * R * ST;
  i8 *ip = img + (size_t) c * rows * ST;
  int *cf = conf + (size_t) c * R;
  u8 *scp = sc + (size_t) c * rows;
  int *tb = tabu + (size_t) c * R * MAXS;
  u8 *act = active + (size_t) c * R;

  __shared__ int j, S, nl, choice, oldself, newconf_j, Tsh, done, do_rep, newk, crossj, crossn, selfn, rep_ok;
  __shared__ int pa[MAXS], pb[MAXS], delta[MAXS];
  __shared__ i8 rj[ST];
  __shared__ u64 rs;
  __shared__ int sgp[MAXG * ST], sgt[MAXG];

  for (int x = tid; x < NG * ST; x += nt) sgp[x] = gp[x];
  for (int x = tid; x < NG; x += nt) sgt[x] = gt[x];
  if (tid == 0) { rs = rng[c]; Tsh = T[c]; done = 0; }
  __syncthreads();

  for (int step = 0; step < P.steps; step++) {
    // 1. choose a conflicted representative and list its swaps
    if (tid == 0) {
      if (Tsh <= 0) done = 1;
      else {
        int s0 = (int) (rnd(&rs) % (u64) R);
        int jj = s0;
        for (int q = 0; q < R; q++) { int k = (s0 + q) % R; if (act[k] && cf[k] > 0) { jj = k; break; } }
        j = jj;
        int bp[4][ST], bn[4] = {0, 0, 0, 0};
        for (int i = 0; i < n; i++) { int b = rp[jj * ST + i] / d; bp[b][bn[b]++] = i; }
        int s = 0; nl = bn[0];
        for (int b = 0; b < 4; b++)
          for (int a = 0; a < bn[b]; a++) for (int c2 = a + 1; c2 < bn[b]; c2++) { pa[s] = bp[b][a]; pb[s] = bp[b][c2]; s++; }
        S = s; oldself = 0; newconf_j = 0;
        do_rep = 0; crossj = 0; crossn = 0; selfn = 0;
        if (P.p_replace > 0.0f && (rnd(&rs) % 1000000) < (u64) (P.p_replace * 1000000.0f)) {
          int s1 = (int) (rnd(&rs) % (u64) R);
          for (int q = 0; q < R; q++) { int k = (s1 + q) % R; if (!act[k]) { newk = k; do_rep = 1; break; } }
        }
        for (int i = 0; i < n; i++) rj[i] = rp[jj * ST + i];
      }
    }
    __syncthreads();
    if (done) break;
    if (do_rep) {
      // orbit replacement: deactivate j, activate newk (its stored row and images)
      const i8 *rn = rp + newk * ST;
      int cj = 0, cn = 0, sn = 0;
      for (int y = tid; y < rows; y += nt) {
        int k = y / NG;
        if (k == j || k == newk || !act[k]) continue;
        const i8 *o = ip + (size_t) y * ST;
        int sj = 0, s2 = 0;
        for (int i = 0; i < n; i++) { sj |= far_(rj[i], o[i], d); s2 |= far_(rn[i], o[i], d); }
        cj += !sj; cn += !s2;
      }
      for (int h = 1 + tid; h < NG; h += nt) {
        const i8 *o = ip + ((size_t) newk * NG + h) * ST;
        int s2 = 0; for (int i = 0; i < n; i++) s2 |= far_(rn[i], o[i], d);
        sn += !s2;
      }
      atomicAdd(&crossj, cj); atomicAdd(&crossn, cn); atomicAdd(&selfn, sn);
      __syncthreads();
      if (tid == 0) {
        int selfj = cf[j] - crossj;
        int dl = (2 * crossn + selfn) - (2 * crossj + selfj);
        rep_ok = dl <= 0 || (rnd(&rs) % 1000000) < (u64) (P.noise * 1000000.0f);
        if (rep_ok) Tsh += dl;
      }
      __syncthreads();
      if (rep_ok) {
        for (int y = tid; y < rows; y += nt) {
          int k = y / NG;
          if (k == j || k == newk || !act[k]) continue;
          const i8 *o = ip + (size_t) y * ST;
          int sj = 0, s2 = 0;
          for (int i = 0; i < n; i++) { sj |= far_(rj[i], o[i], d); s2 |= far_(rn[i], o[i], d); }
          int dc = (!s2) - (!sj);
          if (dc) atomicAdd(&cf[k], dc);
        }
        __syncthreads();
        if (tid == 0) {
          act[j] = 0; act[newk] = 1; cf[j] = 0; cf[newk] = crossn + selfn;
          iter[c]++;
          if (Tsh < best[c]) { best[c] = Tsh; last_imp[c] = iter[c]; done = -1; }
        }
        __syncthreads();
        if (done == -1) {
          for (int x = tid; x < R * ST; x += nt) best_rep[(size_t) c * R * ST + x] = rp[x];
          for (int x = tid; x < R; x += nt) best_active[(size_t) c * R + x] = act[x];
          __syncthreads();
          if (tid == 0) done = 0;
        }
      }
      __syncthreads();
      continue;
    }
    for (int s = tid; s < S; s += nt) delta[s] = 0;

    // 2. separation counts of rep j against every other-orbit row
    for (int y = tid; y < rows; y += nt) {
      int k = y / NG;
      if (k == j || !act[k]) continue;
      const i8 *o = ip + (size_t) y * ST;
      int cnt = 0;
      for (int i = 0; i < n; i++) cnt += far_(rj[i], o[i], d);
      scp[y] = (u8) cnt;
    }
    __syncthreads();

    // 3. score all swaps: cross rows (counted twice) ...
    {
      int groups = nt / S; if (groups < 1) groups = 1;
      for (int t = tid; t < S * groups; t += nt) {
        int s = t % S, part = t / S;
        int a = pa[s], b = pb[s], va = rj[a], vb = rj[b];
        int acc = 0;
        for (int y = part; y < rows; y += groups) {
          int k = y / NG;
          if (k == j || !act[k]) continue;
          const i8 *o = ip + (size_t) y * ST;
          int old = scp[y];
          int nw = old - far_(va, o[a], d) - far_(vb, o[b], d) + far_(vb, o[a], d) + far_(va, o[b], d);
          acc += 2 * ((nw == 0) - (old == 0));
        }
        atomicAdd(&delta[s], acc);
      }
    }
    // ... and the row's own orbit (old self-conflicts, then new for each swap)
    for (int h = 1 + tid; h < NG; h += nt) {
      int sepd = 0;
      for (int i = 0; i < n && !sepd; i++) {
        int v = sgt[h] ? n - 1 - rj[i] : rj[i];
        sepd = far_(rj[sgp[h * ST + i]], v, d);
      }
      if (!sepd) atomicAdd(&oldself, 1);
    }
    for (int t = tid; t < S * (NG - 1); t += nt) {
      int s = t / (NG - 1), h = 1 + t % (NG - 1);
      int a = pa[s], b = pb[s];
      int sepd = 0;
      for (int i = 0; i < n && !sepd; i++) {
        int ci = (i == a) ? rj[b] : (i == b) ? rj[a] : rj[i];
        int p = sgp[h * ST + i];
        int cp = (p == a) ? rj[b] : (p == b) ? rj[a] : rj[p];
        int v = sgt[h] ? n - 1 - ci : ci;
        sepd = far_(cp, v, d);
      }
      if (!sepd) atomicAdd(&delta[s], 1);
    }
    __syncthreads();

    // 4. pick the swap
    if (tid == 0) {
      int it = iter[c];
      int bs = -1, bd = 1 << 30, ties = 0;
      if ((rnd(&rs) % 1000000) < (u64) (P.noise * 1000000.0f)) bs = (int) (rnd(&rs) % (u64) S);
      else {
        for (int s = 0; s < S; s++) {
          int dl = delta[s] - oldself;
          int tabu_ok = tb[j * MAXS + s] <= it || Tsh + dl < best[c];
          if (!tabu_ok) continue;
          if (dl < bd) { bd = dl; bs = s; ties = 1; }
          else if (dl == bd && (rnd(&rs) % (u64) (++ties)) == 0) bs = s;
        }
        if (bs < 0) bs = (int) (rnd(&rs) % (u64) S);
      }
      if (it - last_imp[c] > P.stall) { bs = (int) (rnd(&rs) % (u64) S); last_imp[c] = it; }
      choice = bs;
      tb[j * MAXS + bs] = it + P.tenure_lo + (int) (rnd(&rs) % (u64) (P.tenure_span + 1));
      Tsh += delta[bs] - oldself;
    }
    __syncthreads();

    // 5. apply: conf of other reps, then the row and its images
    {
      int a = pa[choice], b = pb[choice], va = rj[a], vb = rj[b];
      int mine = 0;
      for (int y = tid; y < rows; y += nt) {
        int k = y / NG;
        if (k == j || !act[k]) continue;
        const i8 *o = ip + (size_t) y * ST;
        int old = scp[y];
        int nw = old - far_(va, o[a], d) - far_(vb, o[b], d) + far_(vb, o[a], d) + far_(va, o[b], d);
        int dc = (nw == 0) - (old == 0);
        if (dc) atomicAdd(&cf[k], dc);
        mine += (nw == 0);
      }
      atomicAdd(&newconf_j, mine);
    }
    __syncthreads();
    if (tid == 0) {
      int a = pa[choice], b = pb[choice];
      i8 x = rj[a]; rj[a] = rj[b]; rj[b] = x;
      for (int i = 0; i < n; i++) rp[j * ST + i] = rj[i];
    }
    __syncthreads();
    for (int t = tid; t < NG * n; t += nt) {
      int h = t / n, i = t % n;
      int v = sgt[h] ? n - 1 - rj[i] : rj[i];
      ip[((size_t) j * NG + h) * ST + sgp[h * ST + i]] = (i8) v;
    }
    // new self conflicts of j
    for (int h = 1 + tid; h < NG; h += nt) {
      int sepd = 0;
      for (int i = 0; i < n && !sepd; i++) {
        int v = sgt[h] ? n - 1 - rj[i] : rj[i];
        sepd = far_(rj[sgp[h * ST + i]], v, d);
      }
      if (!sepd) atomicAdd(&newconf_j, 1);
    }
    __syncthreads();
    if (tid == 0) {
      cf[j] = newconf_j;
      iter[c]++;
      moves_done[c]++;
      if (Tsh < best[c]) { best[c] = Tsh; last_imp[c] = iter[c]; done = -1; }
    }
    __syncthreads();
    if (done == -1) {
      for (int x = tid; x < R * ST; x += nt) best_rep[(size_t) c * R * ST + x] = rp[x];
      for (int x = tid; x < R; x += nt) best_active[(size_t) c * R + x] = act[x];
      __syncthreads();
      if (tid == 0) done = 0;
      __syncthreads();
    }
  }
  if (tid == 0) { rng[c] = rs; T[c] = Tsh; }
}

// Initial conflict counts: conf[c][j] = rows (k,h) != (j,e) of active orbits
// not separated from active rep j; T[c] = sum.  One block per chain.
extern "C" __global__ void init_conf(Params P, const i8 *rep, const i8 *img, const u8 *active, int *conf, int *T, int *best)
{
  const int c = blockIdx.x, tid = threadIdx.x, nt = blockDim.x;
  const int n = P.n, d = P.d, R = P.R, NG = P.NG, rows = R * NG;
  const i8 *rp = rep + (size_t) c * R * ST;
  const i8 *ip = img + (size_t) c * rows * ST;
  const u8 *act = active + (size_t) c * R;
  int *cf = conf + (size_t) c * R;
  __shared__ int tot;
  if (tid == 0) tot = 0;
  for (int j = tid; j < R; j += nt) cf[j] = 0;
  __syncthreads();
  for (long long t = tid; t < (long long) R * rows; t += nt) {
    int j = (int) (t / rows), y = (int) (t % rows);
    if (!act[j] || !act[y / NG] || y == j * NG) continue;
    const i8 *a = rp + j * ST, *o = ip + (size_t) y * ST;
    int s = 0;
    for (int i = 0; i < n && !s; i++) s = far_(a[i], o[i], d);
    if (!s) { atomicAdd(&cf[j], 1); atomicAdd(&tot, 1); }
  }
  __syncthreads();
  if (tid == 0) { T[c] = tot; best[c] = tot; }
}
