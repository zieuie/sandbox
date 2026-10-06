# Profiling the GPU DP kernel (2026-10-06)

**Result:** the DP tile kernel (`gpu_dp_solver`, `dp_row`) is **1.58× faster on merlin's RTX 3060
and 1.33× faster on a P600 for heavy fields like 31⁷**. Results are byte-identical to the old
kernel and to the CPU solver. Light fields are about unchanged. **Not deployed yet:** the agents
need a rolling upgrade, so it applies to the next DP field (17⁹, say).

## What the profiler showed

Tool: Nsight Compute 2022.4 (Ubuntu's `nsight-compute`, installed on merlin on 2026-10-06; run as
root, since the driver keeps GPU counters for administrators). Script: `gpu_dp_solver/tests/profile.py`,
which builds a production-shaped interior tile with a random full halo and profiles a few `dp_row`
launches from its middle. One launch computes one tile row.

**31⁷, side 4096 (24,179 transitions), one row on the RTX 3060, before:**

| | |
|---|---|
| Time per row | 1.19 ms (× 4,096 rows = 4.9 s per tile) |
| Memory | L2 71% busy, DRAM 44% busy; compute (SM) only 49% |
| Warp stalls | 73% of cycles waiting on loads (L1TEX scoreboard) |
| L1 hit rate | 25% (L2 79%) |
| Occupancy | 67%: 1,024-thread blocks, with registers and shared memory each allowing one block per SM |
| Grid | 128 blocks on 30 SMs: 4.27 waves, so the last wave is mostly idle |
| Instructions | about 30 thread instructions per (cell, transition) pair |

**Why:** each cell takes the maximum over every transition `(a, b, t)`, reading the predecessor
`du = a·t` rows up and `dv = b·t` columns left, within a halo up to p² = 961 deep. The table was
scanned in its original `(a, b, t)` order, which the result's tie rule requires (the earliest
maximizing transition wins). So consecutive loads hop along a ray (a, b), (2a, 2b), …, landing in
different rows and columns, and the cache barely helps.

## What changed (all byte-identical)

1. **Scan order no longer matters.** "Earliest maximizing transition" holds in any scan order if
   a tie goes to the smaller original id (ids increase with the original order, and a candidate
   of 0 never replaces the initial 0). The host then sorts the table by `(du, dv)`, so consecutive
   loads read the same rows at nearby columns.
   - On the 3060 the L1 hit rate went from 25% to 76%, L2 from 71% to 25% busy, and DRAM from 44%
     to 5.5%.
   - Time alone barely moved, because the limit became instruction issue. That made step 2 pay off.
2. **Fewer instructions per pair.**
   - Each transition carries its precomputed offset (`du·width + dv`), so a load is one
     subtraction from a per-thread base pointer, not a 64-bit index calculation.
   - Interior tiles, which have all predecessors (decided once per tile), skip the per-transition
     bounds checks.
3. **16 warps per block instead of 32** for large tables. That's two blocks per SM, better
   occupancy and a smaller idle last wave. It measured best on both GPUs; `KH_DP_SLICES` (1–32)
   overrides it for experiments.
4. **Pascal (the P600s) keeps the original order for tables under 12,000 transitions,** with plain
   strict `>`. Pascal doesn't cache ordinary loads in L1, so for light fields the sort brings no
   locality while the tie comparison costs about 10%. For heavy fields (31⁷) the sort still wins
   on Pascal, through L2. `KH_DP_ORDER=sorted|original` overrides it.
5. **Pascal reads through the read-only data cache (`__ldg`).** Each launch writes only its own
   row. This made no measurable difference on the P600 and cost about 15% on the 3060, so it's
   compiled for Pascal only.

**Tried and dropped:** 2 or 4 cells per thread, sharing one transition load. No gain over 16 warps
of 1 cell each, and 4 cells per thread was slower.

## Measured (kernel compute time, median of 2–3 runs, interior tiles)

| Field (tile side) | Transitions | RTX 3060: old → new | P600: old → new |
|---|---:|---|---|
| 31⁷ (4096) | 24,179 | 4.64 → 2.94 s (**1.58×**) | 57.0 → 42.8 s (**1.33×**) |
| 23⁷ (4096) | 9,969 | 1.83 → 1.34 s (1.36×) | 18.8 → 16.5 s (1.14×) |
| 17⁹ (8192 on the 3060, 4096 on the P600) | 4,050 | 2.71 → 2.22 s (1.22×) | 6.55 → 5.96 s (1.10×) |
| 13⁹ (4096) | 1,838 | 0.53 → 0.48 s (1.09×) | 2.87 → 2.82 s (1.02×) |
| 7¹³ (4096) | 295 | 0.32 → 0.32 s (1.0×) | 0.78 → 0.73 s (1.07×) |
| 5¹⁵ (2048) | 110 | 0.19 → 0.19 s (1.0×) | 0.21 → 0.23 s (0.9×, noise level) |

Every comparison wrote identical `values.bin`, `choices.bin` and `tile.json`. `make check` (48
random tiles against the CPU `kh_dp_tile`, including edge tiles and wraparound values) passes with
both scan orders.

**For the cluster:** a heavy field's DP is GPU-bound, and merlin does about half of it. So about
1.3–1.6× per GPU means roughly 25–35% less DP time for fields like 31⁷, and about 10–20% for 17⁹.
On top of that, tile placement `ect` (docs/GPU_TILE_PLACEMENT_PLAN.md) adds about 11% from
keeping merlin busy.

## Still on the table

- **Remaining stalls** on the 3060 are about 54% L1 latency, with 53% of issue slots used. A
  shared-memory staging of each warp's transition slice, or of the predecessor rows a slice
  covers, might buy another 1.2–1.5×. It's a bigger change, and best tried on a heavy field.
- **Light fields** (13⁹ and below) are dominated by per-launch overhead: 4,096 launches per tile,
  each short. Several rows per launch isn't possible (row u needs row u−1). Bigger tiles
  (CAMPAIGN_NOTES item 26) are the lever there.
