# GPU matching investigation

Status: **experimental, self-contained.** Nothing outside this folder was modified.
The existing field builder (`../dp_solver/src/field.c`) and the existing KHM1
writer/verifier (`../matching_solver/artifacts.py`) are reused read-only.

## Short answer

Yes. Bipartite matching on these graphs suits a GPU well. On Merlin's RTX 3060
Laptop GPU (6 GiB), an exact matcher finds full matchings in seconds. The same
fields took the 9-node CPU cluster 30–110 minutes. Its certificates pass the
existing independent KHM1 verifier.

**The bigger surprise is that most of the speedup is algorithmic, not
hardware.** A single-threaded CPU port of the same algorithm
(`src/cpu_reference.c`) solves 2^23 in about 4 s on one core. The production
kernel needed 773 s on 2 threads. Two changes account for it:

1. **Greedy start offset.** Each request starts scanning its neighbor list at
   a hashed offset instead of `k = 0`. Starting at 0, every request fights over
   the same early neighbors that are already taken. On 2^23 the offset cuts
   greedy edge scans from 8.6 G to 66 M, and single-core greedy time from 70 s
   to 1.8 s. Greedy then leaves only about 0.03% of requests unmatched.
2. **APFB phases instead of shortest-layer Hopcroft–Karp.** One BFS starts from
   all free requests at once. Each tree carries its root and stops as soon as
   it claims a free right vertex. A typical field then needs **1–2 phases**.

## Results (RTX 3060 Laptop, pinned campaign polynomials, same KHD1 inputs)

"Cluster" is the measured wall time of the completed `match_distributed` run
on 9 nodes (continuous campaign). "`match` 2 threads" is the measured single-node
run from the overnight campaign. "GPU solve" excludes field
construction (CPU, shown separately) and certificate writing.

| Field | q | F | Cluster (9 nodes) | `match` 2 threads | GPU solve | GPU phases | Field build |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2^23 | 8.4 M | 2048 | 906 s | 774 s | **0.19 s** | 2 | 0.1 s |
| 3^15 | 14.3 M | 2187 | 1964 s | 2902 s | **0.34 s** | 2 | 0.9 s |
| 11^7 | 19.5 M | 1331 | 2306 s | 2039 s | **0.48 s** | 2 | 0.8 s |
| 2^25 | 33.6 M | 4096 | 5264 s | 10790 s | **0.81 s** | 2 | 0.2 s |
| 7^9 | 40.4 M | 2401 | 4031 s | 4290 s | **0.88 s** | 2 | 3.7 s |
| 5^11 | 48.8 M | 3125 | 6698 s | 12823 s | **1.07 s** | 1 | 8.4 s |
| 13^7 | 62.7 M | 2197 | 6584 s | — | **1.53 s** | 2 | 7.3 s |
| 3^17 | 129 M | 6561 | never run | — | **3.27 s** | 2 | 10.8 s |
| 2^27 | 134 M | 8192 | never run | — | **3.75 s** | 2 | 1.4 s |

3^17 and 2^27 used the first automatic polynomial; the others used the same
polynomial as the campaign certificate. Raw per-run JSON (phase logs, scans,
timings) is in `results/runs.jsonl` and `results/verified.jsonl`.

Same-algorithm CPU vs GPU, on 2^23:

| Implementation | Solve | Edge scans |
| --- | ---: | ---: |
| Production `kh_match_kernel`, 1 thread | see `results/production_2_23_1thread.log` | |
| `cpu_reference`, 1 core, offset greedy + APFB | 4.10 s | 143 M |
| `cpu_reference`, 1 core, greedy from k=0 + APFB | 71.3 s | 8.67 G |
| GPU, offset greedy + APFB | 0.19 s | 421 M* |
| GPU, greedy from k=0 + APFB | 2.02 s | 8.89 G |

\*The GPU counts whole 32-wide warp chunks, so it overcounts compared with the
serial CPU's early exit.

The like-for-like GPU advantage is about **20×** over one CPU core, from
latency hiding on random `right[]` gathers. Against a well-threaded CPU APFB it
would probably be closer to 3–6×, since the CPU side becomes memory-bound.

## Correctness

* **Algorithm.** Greedy uses `atomicCAS` on `right[v]`. Each phase is
  multi-source BFS: a left vertex joins exactly one tree, by `atomicCAS` on
  `root[w]`. A tree that reaches a free right vertex first claims its root, then
  reserves the vertex (`FREE → RESERVED`). If the reservation fails it releases
  the root. Trees are vertex-disjoint and reserved endpoints are distinct, so
  all claimed paths flip in parallel without conflicts. A phase that claims
  nothing has explored the full alternating reachability set from every free
  request, so the matching is maximum (Berge). In that case the visited set is
  the Hall witness, written as the KHM1 obstruction bitmap.
* **Self-check.** An on-device check re-derives every selected edge from the
  cell table and checks two-way ownership.
* **Independent verification.** `-o` writes a real KHM1 certificate (same
  header and packing as `kh_match_kernel`) and runs
  `matching_solver.artifacts.verify` on it.

## Memory: what fits where

Device bytes are about `8·q + 24·N` (cells + right, plus left, choice, root,
parent, via-k, and two frontier queues). Every field here has N = q, so about
**32 bytes per field element**. Per-root claim state is sized by the free
requests left after greedy, which is tiny.

| GPU (fleet) | VRAM | Largest field that fits |
| --- | ---: | --- |
| RTX 3060 Laptop (Merlin) | 6 GiB | ~180 M → 3^17, 2^27 ✓; 17^7 (410 M) ✗ |
| GTX 1050 Ti Mobile (pellinore) | 4 GiB | ~120 M → 13^7 ✓; 3^17 ✗ |
| Quadro P600 (.101–.108) | 2 GiB | ~60 M → 5^11 ✓; 13^7 borderline |

Fleet notes from a read-only probe: `.101` has a working driver. `.104`
reports an NVML driver/library mismatch, which usually needs a reboot.
`.108`'s NVIDIA driver is not loaded. The P600 and 1050 Ti are Pascal (sm_61).
They need the CUDA 12.x toolchain (CUDA 13 dropped Pascal) and the 580 driver
branch, which is their last.

Remaining large fields (17^7, 2^29, 19^7, 7^11, 11^9) exceed any single GPU
here. Options, roughly in order of effort:

1. **CPU APFB on Merlin.** About 32 B/element means 2^29 needs ~17 GiB, which
   fits in Merlin's 38 GiB. Extrapolating the 2^23 single-core result, this is
   minutes to tens of minutes, not hours. 7^11 and 11^9 (~64–75 GiB) do not
   fit.
2. **Trim state.** Bitmap frontiers instead of two u32 queues (−8 B), and
   parent/via-k packed or recomputed (−2 to −6 B). That gets near 20 B/element,
   which raises the 3060's ceiling to ~300 M. That is still short of 17^7
   (~8 GiB).
3. **Multi-GPU / out-of-core.** Partition `right[]` and cells across GPUs with
   batched frontier exchange. This is the same communication problem as
   `matching_solver_multi/`, but each node computes ~20× faster.

## Recommendation

* The cheapest large win is **porting the two algorithmic changes into the
  existing C matcher**: the hashed greedy offset, and early-terminating
  multi-source BFS (APFB). That speeds up the CPU campaign by one to two
  orders of magnitude with no new hardware dependency. This investigation did
  not touch the production code.
* Use the GPU path for fields up to ~180 M on Merlin. A cluster adapter is
  straightforward once this is past the experiment stage. The orchestration is
  already done; matching itself drops to seconds, so field construction (CPU,
  up to ~11 s here) becomes the dominant cost.
* For the DP question, see the section below.

## What about the DP?

Not measured here; this is a reading of `dp_solver/src/dp_tile.c`, not a
benchmark. The tile recurrence is
`value[u][v] = max over transitions (value[u-du][v-dv] + gain)`, with both
offsets strictly positive. Each cell depends only on strictly earlier rows (and
earlier anti-diagonals), so a whole anti-diagonal is independent. The local
kernel already exploits this with pthreads. That pattern maps naturally onto a
GPU wavefront, so "no good for DP" isn't obvious. The concerns are:

* uint64 max-plus arithmetic, which Pascal handles slowly;
* a halo of up to `p^2` in each direction, which limits on-chip reuse;
* tiles admitted at up to 2 GiB, which won't fit a P600.

A small benchmark of one tile would settle it. That would be a separate
experiment.

## Layout and usage

```
gpu_match_solver/
  gpu_match.py          CuPy driver + CUDA kernels (greedy, APFB expand/augment, check)
  src/field_export.c    builds a field with the production builder, dumps cells (KHGF)
  src/cpu_reference.c   single-thread CPU port of the same algorithm, for comparison
  results/              JSON run logs
  Makefile              build, venv, smoke check
```

```sh
cd king_hamming/gpu_match_solver
make                 # builds build/kh_field_export and build/kh_cpu_reference
make venv            # local .venv with cupy-cuda12x[ctk] (no system CUDA toolkit needed)
make check           # 5^3, 7^5, 13^5 with independent KHM1 verification
.venv/bin/python gpu_match.py ../examples/7_5.khdp -o /tmp/7_5.khmatch
```

`gpu_match.py` options: `--poly` (pin a polynomial, default `auto` = first
candidate), `--no-verify` (skip the slow Python verifier), `--salt 0` (disable
the greedy offset), `--json PATH`, and `--keep-field PATH`.

Limits of this prototype: one polynomial per run, with no automatic retry
after an obstruction. No checkpoints, though runs take seconds. Requires
`F ≤ 65535` (16-bit choices) and `q ≤ 2^32`. Single GPU only.
