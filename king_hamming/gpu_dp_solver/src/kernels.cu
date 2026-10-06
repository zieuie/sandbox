// Exact GPU DP tile row kernel. Every transition has du,dv >= 1, so all cells of one
// row depend only on earlier rows and are independent. Each block owns 32 consecutive
// cells; its warps split the transition table into contiguous slices and the slices merge.
//
// The result must equal kh_dp_tile's sequential scan: strict '>' in the original transition
// order, so the earliest maximizing transition wins (and choice 0 when nothing beats the
// initial 0). That rule doesn't depend on scan order if a tie goes to the smaller original
// id (ids increase with the original order), so the host may sort the table by (du, dv): a
// warp's consecutive loads then read the same predecessor rows at nearby columns and hit in
// cache. It does so except for small tables on Pascal ('ordered'), where the tie comparison
// costs more than the locality returns; there the original order and strict '>' are used.
// Compiled at build time by cuda/embed.py (NVRTC); no headers are available here.

typedef unsigned int u32;
typedef unsigned long long u64;

struct hot_t {
    u64 offset;     // du * width + dv: the predecessor is this many values before the cell
    u32 id;         // original transition id (1-based; ids increase with the original order)
    u32 gain;
};

struct bound_t {    // only consulted near the DP's zero edges (checked != 0)
    u32 du;
    u32 dv;
};

#define MAX_SLICES 32

// Earlier rows and the transition table are read-only within one launch (it writes only row u).
// On Pascal (the P600s) ordinary global loads skip L1, so reads there go through the read-only
// data cache. On Turing and later L1 already caches them, and the read-only path measured slower.
#if defined(__CUDA_ARCH__) && __CUDA_ARCH__ < 700
#define READ(pointer) __ldg(pointer)
#else
#define READ(pointer) (*(pointer))
#endif

__device__ __forceinline__ hot_t load_transition(const hot_t *pointer) {
    hot_t t;
    t.offset = READ(&pointer->offset);
    t.id = READ(&pointer->id);
    t.gain = READ(&pointer->gain);
    return t;
}

// Is (value, id) better than (best, best_id)? Larger value wins; equal positive values go to the
// earlier transition. An unchosen state is (0, 0), which a candidate of 0 never replaces.
__device__ __forceinline__ bool better(u64 value, u32 id, u64 best, u32 best_id) {
    return value > best || (value == best && best_id != 0 && id < best_id);
}

extern "C" __global__ void dp_row(u64 *values, u32 *choices, const hot_t *transitions, const bound_t *bounds,
                                  u32 count, u32 checked, u32 ordered, u32 u, u32 first_u, u32 first_v,
                                  u32 last_v, u32 origin_u, u32 origin_v, u64 width, u64 tile_width) {
    __shared__ u64 best_shared[MAX_SLICES][32];
    __shared__ u32 id_shared[MAX_SLICES][32];
    u32 lane = threadIdx.x & 31;
    u32 slice = threadIdx.x >> 5;
    u32 slices = blockDim.x >> 5;
    u32 v = first_v + blockIdx.x * 32 + lane;
    bool active = v <= last_v;
    u64 best = 0;
    u32 best_id = 0;
    if (active) {
        u32 begin = (u32)((u64)count * slice / slices);
        u32 end = (u32)((u64)count * (slice + 1) / slices);
        const u64 *cell = values + (u64)(u - origin_u) * width + (v - origin_v);
        if (checked) {   // near the DP's zero edges: skip predecessors below row or column 1
            for (u32 i = begin; i < end; ++i) {
                bound_t b = bounds[i];
                if (b.du > u || b.dv > v) continue;
                hot_t t = load_transition(transitions + i);
                u64 candidate = READ(cell - (long long)t.offset) + t.gain;
                if (better(candidate, t.id, best, best_id)) {
                    best = candidate;
                    best_id = t.id;
                }
            }
        } else if (ordered) {   // original order: strict '>' is already the earliest-wins rule
            for (u32 i = begin; i < end; ++i) {
                hot_t t = load_transition(transitions + i);
                u64 candidate = READ(cell - (long long)t.offset) + t.gain;
                if (candidate > best) {
                    best = candidate;
                    best_id = t.id;
                }
            }
        } else {         // every predecessor exists: one subtraction per load, no bounds
            for (u32 i = begin; i < end; ++i) {
                hot_t t = load_transition(transitions + i);
                u64 candidate = READ(cell - (long long)t.offset) + t.gain;
                if (better(candidate, t.id, best, best_id)) {
                    best = candidate;
                    best_id = t.id;
                }
            }
        }
    }
    best_shared[slice][lane] = best;
    id_shared[slice][lane] = best_id;
    __syncthreads();
    if (slice == 0 && active) {
        u64 value = 0;
        u32 chosen = 0;
        for (u32 s = 0; s < slices; ++s) {
            if (better(best_shared[s][lane], id_shared[s][lane], value, chosen)) {
                value = best_shared[s][lane];
                chosen = id_shared[s][lane];
            }
        }
        values[(u64)(u - origin_u) * width + (v - origin_v)] = value;
        choices[(u64)(u - first_u) * tile_width + (v - first_v)] = chosen;
    }
}
