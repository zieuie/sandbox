// Exact GPU block matching kernels (see ../docs/GPU_BLOCK_MATCHING.md): the field is cut into blocks,
// each matched against a window of right vertices with offset greedy and APFB phases
// (Deveci et al.). Compiled at build time by cuda/embed.py (NVRTC); no headers are available here.

typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned short u16;
#define FREE 0xFFFFFFFFu
#define RESERVED 0xFFFFFFFEu
#define FULL 0xFFFFFFFFu

// Same map as kh_neighbor(): zero fixed, nonzero labels multiplied by X^-coset.
__device__ __forceinline__ u32 shift(u32 label, u32 coset, u32 qm1) {
    if (label == 0) return 0;
    u32 x = label - 1;
    x = x >= coset ? x - coset : x + (qm1 - coset);
    return x + 1;
}

__device__ __forceinline__ u32 mix(u32 x) {
    x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16;
    return x;
}

// Phase setup: clear tree membership and list free requests (any order).
extern "C" __global__ void collect_roots(u32 n, const u32 *left, u32 *root, u32 *roots, u32 *count) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u >= n) return;
    if (left[u] == FREE) {
        u32 at = atomicAdd(count, 1u);
        roots[at] = u;
        root[u] = at;
    } else {
        root[u] = FREE;
    }
}

// Vertex-disjoint trees make every claimed path independent; flip each in one thread.
extern "C" __global__ void augment(u32 nroots, const u32 *roots, u32 *left, u16 *choice, u32 *right,
                                   const u32 *parent, const u16 *viak, const u32 *rootdone,
                                   const u32 *end_u, const u32 *end_v, const u16 *end_k,
                                   u32 *matched, u32 *longest) {
    u32 i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= nroots || !rootdone[i]) return;
    u32 r = roots[i];
    u32 cur = end_u[i], v = end_v[i];
    u16 k = end_k[i];
    u32 length = 1;
    for (;;) {
        u32 old = left[cur];
        left[cur] = v;
        choice[cur] = k;
        right[v] = cur;
        if (cur == r) break;
        k = viak[cur];
        v = old;
        cur = parent[cur];
        ++length;
    }
    atomicAdd(matched, 1u);
    atomicMax(longest, length);
}

// ---------------------------------------------------------------------------------------------
// Block mode (docs/GPU_BLOCK_MATCHING.md). A block is a set of requests (its own cell range plus
// a few imported requests) matched against one contiguous window [lo, hi) of right vertices.
// Right state is indexed by v - lo. A request's neighbors inside the window are one or two
// contiguous runs of its ascending cell row (plus the zero label), so the kernels scan only those.
// left[] holds: local right index, FREE (unmatched, a root), or INACTIVE (not part of this block's
// search: its right, if any, lies in another block). choice[] always holds the global row index
// k, with 0xFFFF meaning unmatched; F <= 65534 keeps that sentinel out of range.
// ---------------------------------------------------------------------------------------------
#define INACTIVE 0xFFFFFFFDu
#define NOCHOICE 0xFFFFu

struct bgraph_t {
    const u32 *rows;     // row slot s occupies rows[s*f .. s*f+f), ascending labels
    const u32 *lfirst;   // first local own index of each clipped DP block
    const u32 *lcoset;   // first coset of each clipped DP block
    const u32 *lwidth;   // clipped cells per coset of each clipped DP block
    const u32 *icoset;   // imported request i: coset
    const u32 *islot;    // imported request i: row slot
    u32 nblocks;
    u32 f;
    u32 qm1;
    u32 nown;            // own requests, local indices [0, nown); imports follow
    u32 n;               // own + imported
    u32 lo;
    u32 hi;
    u32 pad;
};

struct win_t {
    u32 start[3];
    u32 len[3];
    u32 total;
};

__device__ __forceinline__ void request(const bgraph_t &g, u32 u, u32 *coset, u32 *slot) {
    if (u >= g.nown) {
        *coset = g.icoset[u - g.nown];
        *slot = g.islot[u - g.nown];
        return;
    }
    u32 lo = 0, hi = g.nblocks;
    while (lo + 1 < hi) {
        u32 m = (lo + hi) >> 1;
        if (g.lfirst[m] <= u) lo = m; else hi = m;
    }
    u32 off = u - g.lfirst[lo];
    u32 w = g.lwidth[lo];
    *coset = g.lcoset[lo] + off / w;
    *slot = off % w;
}

// Number of row entries strictly below x (the row is ascending).
__device__ __forceinline__ u32 lower_bound(const u32 *row, u32 f, u64 x) {
    u32 lo = 0, hi = f;
    while (lo < hi) {
        u32 m = (lo + hi) >> 1;
        if ((u64)row[m] < x) lo = m + 1; else hi = m;
    }
    return lo;
}

// Row index ranges whose neighbor lies in [lo, hi): zero labels (if the window holds right 0),
// then the circular label interval that maps onto the nonzero rights of the window.
__device__ __forceinline__ void window(const u32 *row, u32 f, u32 coset, u32 lo, u32 hi, u32 qm1, win_t *w) {
    w->start[0] = w->start[1] = w->start[2] = 0;
    w->len[0] = w->len[1] = w->len[2] = 0;
    if (lo == 0) w->len[0] = lower_bound(row, f, 1);
    u32 tlo = lo ? lo - 1 : 0;
    u32 thi = hi ? hi - 1 : 0;
    u64 count = thi > tlo ? thi - tlo : 0;
    if (count) {
        u64 s = ((u64)tlo + coset) % qm1;          // labels s+1 .. s+count, wrapping past qm1 to 1
        if (s + count <= qm1) {
            u32 a = lower_bound(row, f, s + 1), b = lower_bound(row, f, s + count + 1);
            w->start[1] = a; w->len[1] = b - a;
        } else {
            u32 a = lower_bound(row, f, s + 1);
            w->start[1] = a; w->len[1] = f - a;
            u32 b = lower_bound(row, f, s + count - qm1 + 1);
            u32 c = lower_bound(row, f, 1);
            w->start[2] = c; w->len[2] = b - c;
        }
    }
    w->total = w->len[0] + w->len[1] + w->len[2];
}

__device__ __forceinline__ u32 window_k(const win_t &w, u32 j) {
    if (j < w.len[0]) return w.start[0] + j;
    j -= w.len[0];
    if (j < w.len[1]) return w.start[1] + j;
    return w.start[2] + j - w.len[1];
}

// Same contract as greedy(), restricted to the window. Counters: [0] matched, u64 scans.
extern "C" __global__ void greedy_w(bgraph_t g, u32 *left, u16 *choice, u32 *right,
                                    u32 *matched, u64 *scans, u32 salt) {
    u32 warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    u32 lane = threadIdx.x & 31;
    if (warp >= g.n) return;
    u32 u = warp;
    if (left[u] != FREE) return;
    u32 coset, slot;
    request(g, u, &coset, &slot);
    const u32 *row = g.rows + (u64)slot * g.f;
    win_t w;
    window(row, g.f, coset, g.lo, g.hi, g.qm1, &w);
    if (w.total == 0) return;
    u32 start = salt ? mix(u ^ salt) % w.total : 0;
    u32 scanned = 0;
    for (u32 base = 0; base < w.total; base += 32) {
        u32 j = base + lane;
        u32 v = 0, k = 0;
        bool free = false;
        if (j < w.total) {
            u32 jj = j + start;
            if (jj >= w.total) jj -= w.total;
            k = window_k(w, jj);
            v = shift(row[k], coset, g.qm1);
            free = right[v - g.lo] == FREE;
        }
        scanned += min(32u, w.total - base);
        unsigned m = __ballot_sync(FULL, free);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 kl = __shfl_sync(FULL, k, l);
            u32 got = 0;
            if (lane == 0) got = atomicCAS(&right[vv - g.lo], FREE, u);
            got = __shfl_sync(FULL, got, 0);
            if (got == FREE) {
                if (lane == 0) {
                    left[u] = vv - g.lo;
                    choice[u] = (u16)kl;
                    atomicAdd(matched, 1u);
                    atomicAdd(scans, (u64)scanned);
                }
                return;
            }
            m &= m - 1;
        }
    }
    if (lane == 0) atomicAdd(scans, (u64)scanned);
}

// Same contract as expand(), restricted to the window; viak/end_k hold global row indices.
extern "C" __global__ void expand_w(bgraph_t g, u32 nfront, const u32 *front, u32 *next, u32 *ntail,
                                    u32 *right, u32 *root, u32 *parent, u16 *viak,
                                    u32 *rootdone, u32 *end_u, u32 *end_v, u16 *end_k,
                                    u32 *found, u64 *scans) {
    u32 warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    u32 lane = threadIdx.x & 31;
    if (warp >= nfront) return;
    u32 u = front[warp];
    u32 r = root[u];
    volatile u32 *done = rootdone;
    if (done[r]) return;
    u32 coset, slot;
    request(g, u, &coset, &slot);
    const u32 *row = g.rows + (u64)slot * g.f;
    win_t win;
    window(row, g.f, coset, g.lo, g.hi, g.qm1, &win);
    u32 scanned = 0;
    unsigned lower = (1u << lane) - 1;
    for (u32 base = 0; base < win.total; base += 32) {
        u32 stop = 0;
        if (lane == 0) stop = done[r];
        if (__shfl_sync(FULL, stop, 0)) break;
        u32 j = base + lane;
        u32 v = 0, k = 0, w = RESERVED;
        if (j < win.total) {
            k = window_k(win, j);
            v = shift(row[k], coset, g.qm1) - g.lo;
            w = right[v];
        }
        scanned += min(32u, win.total - base);
        unsigned m = __ballot_sync(FULL, w == FREE);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 kl = __shfl_sync(FULL, k, l);
            u32 status = 0;
            if (lane == 0) {
                if (atomicCAS(&rootdone[r], 0u, 1u) == 0u) {
                    if (atomicCAS(&right[vv], FREE, RESERVED) == FREE) {
                        end_u[r] = u;
                        end_v[r] = vv;
                        end_k[r] = (u16)kl;
                        atomicAdd(found, 1u);
                        status = 1;
                    } else {
                        atomicExch(&rootdone[r], 0u);
                    }
                } else {
                    status = 2;
                }
            }
            status = __shfl_sync(FULL, status, 0);
            if (status) {
                if (lane == 0) atomicAdd(scans, (u64)scanned);
                return;
            }
            m &= m - 1;
        }
        bool disc = false;
        if (w != FREE && w != RESERVED && root[w] == FREE && atomicCAS(&root[w], FREE, r) == FREE) {
            parent[w] = u;
            viak[w] = (u16)k;
            disc = true;
        }
        unsigned d = __ballot_sync(FULL, disc);
        if (d) {
            u32 at = 0;
            if (lane == 0) at = atomicAdd(ntail, (u32)__popc(d));
            at = __shfl_sync(FULL, at, 0);
            if (disc) next[at + __popc(d & lower)] = w;
        }
    }
    if (lane == 0) atomicAdd(scans, (u64)scanned);
}

// Rebuild left/right from the host's global choices. Own requests whose right lies outside the
// window, and own requests that are free, are INACTIVE; free imports are roots. Counter [0]
// counts inconsistencies (an import outside the window, a right claimed twice).
extern "C" __global__ void restore_w(bgraph_t g, const u16 *choice, u32 *left, u32 *right, u32 *bad) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u >= g.n) return;
    u32 c = choice[u];
    bool own = u < g.nown;
    if (c == NOCHOICE) {
        left[u] = own ? INACTIVE : FREE;
        return;
    }
    u32 coset, slot;
    request(g, u, &coset, &slot);
    u32 v = shift(g.rows[(u64)slot * g.f + c], coset, g.qm1);
    if (v < g.lo || v >= g.hi) {
        left[u] = INACTIVE;
        if (!own) atomicAdd(bad, 1u);
        return;
    }
    left[u] = v - g.lo;
    if (atomicExch(&right[v - g.lo], u) != FREE) atomicAdd(bad, 1u);
}

// Number of unmatched roots (FREE requests).
extern "C" __global__ void count_free(u32 n, const u32 *left, u32 *count) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u < n && left[u] == FREE) atomicAdd(count, 1u);
}

// Independent consistency check: every active matched request's edge exists, lies in the
// window, and is owned both ways. counters: bad, matched.
extern "C" __global__ void check_w(bgraph_t g, const u32 *left, const u16 *choice, const u32 *right,
                                   u32 *bad, u32 *matched) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u >= g.n) return;
    u32 vl = left[u];
    if (vl == FREE || vl == INACTIVE) {
        if (vl == FREE && choice[u] != NOCHOICE) atomicAdd(bad, 1u);
        return;
    }
    atomicAdd(matched, 1u);
    u32 coset, slot;
    request(g, u, &coset, &slot);
    u32 k = choice[u];
    if (k >= g.f) { atomicAdd(bad, 1u); return; }
    u32 v = shift(g.rows[(u64)slot * g.f + k], coset, g.qm1);
    if (v < g.lo || v >= g.hi || v - g.lo != vl || right[vl] != u) atomicAdd(bad, 1u);
}
