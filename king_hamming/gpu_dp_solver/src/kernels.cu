// Exact GPU DP tile row kernel. Every transition has du,dv >= 1, so all cells of one
// row depend only on earlier rows and are independent. Each block owns 32 consecutive
// cells; its warps split the ordered transition scan into contiguous slices, and the
// slices merge in order with strict '>' so the earliest maximizing transition wins,
// exactly like kh_dp_tile's sequential strict-improvement scan.
// Compiled at build time by cuda/embed.py (NVRTC); no headers are available here.

typedef unsigned int u32;
typedef unsigned long long u64;

struct hot_t {
    u32 du;
    u32 dv;
    u32 id;
    u32 gain;
};

#define MAX_SLICES 32

extern "C" __global__ void dp_row(u64 *values, u32 *choices, const hot_t *transitions, u32 count,
                                  u32 u, u32 first_u, u32 first_v, u32 last_v,
                                  u32 origin_u, u32 origin_v, u64 width, u64 tile_width) {
    __shared__ u64 best_shared[MAX_SLICES][32];
    __shared__ u32 index_shared[MAX_SLICES][32];
    u32 lane = threadIdx.x & 31;
    u32 slice = threadIdx.x >> 5;
    u32 slices = blockDim.x >> 5;
    u32 v = first_v + blockIdx.x * 32 + lane;
    bool active = v <= last_v;
    u64 best = 0;
    u32 index = 0xFFFFFFFFu;
    if (active) {
        u32 begin = (u32)((u64)count * slice / slices);
        u32 end = (u32)((u64)count * (slice + 1) / slices);
        for (u32 i = begin; i < end; ++i) {
            hot_t t = transitions[i];
            if (t.du > u || t.dv > v) continue;
            u64 candidate = values[(u64)(u - t.du - origin_u) * width + (v - t.dv - origin_v)] + t.gain;
            if (candidate > best) {
                best = candidate;
                index = i;
            }
        }
    }
    best_shared[slice][lane] = best;
    index_shared[slice][lane] = index;
    __syncthreads();
    if (slice == 0 && active) {
        u64 value = 0;
        u32 chosen = 0xFFFFFFFFu;
        for (u32 s = 0; s < slices; ++s) {
            if (best_shared[s][lane] > value) {
                value = best_shared[s][lane];
                chosen = index_shared[s][lane];
            }
        }
        values[(u64)(u - origin_u) * width + (v - origin_v)] = value;
        choices[(u64)(u - first_u) * tile_width + (v - first_v)] = chosen == 0xFFFFFFFFu ? 0 : transitions[chosen].id;
    }
}
