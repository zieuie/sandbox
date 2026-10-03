// Exact GPU matching kernels: offset greedy, then APFB phases (Deveci et al.):
// multi-source BFS where every tree carries its root, stops once that root claims a
// free right endpoint, and all claimed vertex-disjoint paths are flipped in parallel.
// Compiled at build time by cuda/embed.py (NVRTC); no headers are available here.

typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned short u16;
#define FREE 0xFFFFFFFFu
#define RESERVED 0xFFFFFFFEu
#define FULL 0xFFFFFFFFu

struct graph_t {
    const u32 *cells;
    const u64 *bfirst;
    const u32 *bcoset;
    const u32 *bwidth;
    u32 nblocks;
    u32 f;
    u32 qm1;
    u32 n;
};

// Binary search over compact request blocks, as kh_request() does.
__device__ __forceinline__ void decode(const graph_t &g, u32 u, u32 *coset, u32 *cell) {
    u32 lo = 0, hi = g.nblocks;
    while (lo + 1 < hi) {
        u32 m = (lo + hi) >> 1;
        if (g.bfirst[m] <= u) lo = m; else hi = m;
    }
    u64 off = (u64)u - g.bfirst[lo];
    u32 w = g.bwidth[lo];
    *coset = g.bcoset[lo] + (u32)(off / w);
    *cell = (u32)(off % w);
}

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

// One warp per free request: coalesced cell reads, first free neighbor from a hashed
// offset claimed with atomicCAS. Counters: [0] matched, [2..3] u64 scans.
extern "C" __global__ void greedy(graph_t g, u32 *left, u16 *choice, u32 *right,
                                  u32 *matched, u64 *scans, u32 salt) {
    u32 warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    u32 lane = threadIdx.x & 31;
    if (warp >= g.n) return;
    u32 u = warp;
    if (left[u] != FREE) return;
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    const u32 *row = g.cells + (u64)cell * g.f;
    u32 start = salt ? mix(u ^ salt) % g.f : 0;
    u32 scanned = 0;
    for (u32 base = 0; base < g.f; base += 32) {
        u32 k = base + lane;
        u32 kk = k + start;
        if (kk >= g.f) kk -= g.f;
        u32 v = 0;
        bool free = false;
        if (k < g.f) {
            v = shift(row[kk], coset, g.qm1);
            free = right[v] == FREE;
        }
        scanned += min(32u, g.f - base);
        unsigned m = __ballot_sync(FULL, free);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 kl = __shfl_sync(FULL, kk, l);
            u32 got = 0;
            if (lane == 0) got = atomicCAS(&right[vv], FREE, u);
            got = __shfl_sync(FULL, got, 0);
            if (got == FREE) {
                if (lane == 0) {
                    left[u] = vv;
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

// One BFS level. root[w] holds the index of w's tree root; per-root state is indexed by it.
extern "C" __global__ void expand(graph_t g, u32 nfront, const u32 *front, u32 *next, u32 *ntail,
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
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    const u32 *row = g.cells + (u64)cell * g.f;
    u32 scanned = 0;
    unsigned lower = (1u << lane) - 1;
    for (u32 base = 0; base < g.f; base += 32) {
        u32 stop = 0;
        if (lane == 0) stop = done[r];
        if (__shfl_sync(FULL, stop, 0)) break;
        u32 k = base + lane;
        u32 v = 0, w = RESERVED;
        if (k < g.f) {
            v = shift(row[k], coset, g.qm1);
            w = right[v];
        }
        scanned += min(32u, g.f - base);
        unsigned m = __ballot_sync(FULL, w == FREE);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 status = 0;
            if (lane == 0) {
                // Claim the root first, then reserve the endpoint; release the root on failure.
                if (atomicCAS(&rootdone[r], 0u, 1u) == 0u) {
                    if (atomicCAS(&right[vv], FREE, RESERVED) == FREE) {
                        end_u[r] = u;
                        end_v[r] = vv;
                        end_k[r] = (u16)(base + l);
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

// Independent on-device consistency check: every selected edge exists and is owned both ways.
extern "C" __global__ void check(graph_t g, const u32 *left, const u16 *choice, const u32 *right,
                                 u32 *bad, u32 *matched) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u >= g.n) return;
    u32 v = left[u];
    if (v == FREE) return;
    atomicAdd(matched, 1u);
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    u32 k = choice[u];
    if (k >= g.f || shift(g.cells[(u64)cell * g.f + k], coset, g.qm1) != v || right[v] != u)
        atomicAdd(bad, 1u);
}
