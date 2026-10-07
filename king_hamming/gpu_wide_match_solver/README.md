# GPU wide matching solver

Exact perfect matching for fields past [gpu_block_match_solver](../gpu_block_match_solver/README.md)'s
limits, which is what 7¹³ needs:

| Limit | Block solver | Wide solver |
|---|---|---|
| Choices per cell F | ≤ 65,534 (16-bit) | up to 2³² − 2 (32-bit on the GPU) |
| Field size q | < 2³⁶ | < 2⁴⁰ |
| Field rows in host memory | all used rows at once (166 GB for 7¹³) | one pass at a time (`--row-bytes`) |
| Choice storage on the host | 2 bytes per request, plus the payload written at the end | the payload itself, written in place |
| Verifier | `kh_verify_khm1`: all rows at once, one thread | `kh_verify_wide`: passes, threads |

It is a copy of the block solver, so it uses the same block layout, kernels and exchange. Fields
that the block solver can do should stay with it, because this one is slower for them:
- **Wider choices** add 4 bytes per request on the GPU (35 instead of 31), so blocks are about
  10% smaller.
- **Passes** cost a walk over all q labels each, plus about 2 s of spot checks. That's 29 s
  against 7 s on 13⁷ in 9 forced passes.
- **The scattered in-place writes** replace one sequential write.

The design and the 7¹³ numbers are in
[docs/GPU_WIDE_MATCHING_PLAN.md](../docs/GPU_WIDE_MATCHING_PLAN.md).

**In the campaign** (built 2026-10-06; deployed when merlin's agent next upgrades):
- **Program:** `match_gpu_wide` (`adapter.py`, bridge `cluster_solver.py`, spec builder `submit.py`).
- **Routing:** the feeder plans it when the block matcher refuses a field, on a host with the
  GPU, the memory and the free disk for the certificate ([docs/GPU.md](../docs/GPU.md),
  "Feeder routing").
- **Publication:** the bridge passes `--payload-offset`, then writes the KHM1 header and
  checksum around the payload in place (`artifacts.publish_in_place`).
- **Linking:** the agent hard-links the result into its blob store (`store_blob(link=True)`),
  and the feeder hard-links the archive from it (`retrieve(local_roots=...)`).
- **Verification:** `artifacts.verify` uses `kh_verify_wide` whenever `kh_verify_khm1` can't
  hold the field.
- **End to end:** `tests/check_cluster.py --run` runs a forced multi-pass 13⁵ through a real
  leader and GPU agent, and verifies it.

```sh
make -C king_hamming/gpu_wide_match_solver          # kh_gpu_wide_kernel and kh_verify_wide
make -C king_hamming/gpu_wide_match_solver check    # builder unit test, verifier checks, solver checks
make -C king_hamming/cuda toolchain && make -C king_hamming/gpu_wide_match_solver images  # after editing kernels.cu
```

```sh
./kh_gpu_wide_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr] [--threads N] [--max-bytes N]
    [--row-bytes N] [--device N] [--salt N] [--block-device-bytes N] [--block-requests N]
    [--max-rounds N] [--max-residual N] [--max-rescue-passes N]
./kh_verify_wide P R C0,...,Cr BLOCKS.txt FILE OFFSET [--threads N] [--row-bytes N]
```

**Contract:** the same blocks file, packed payload and metadata JSON as the block solver
(`"engine": "gpu-wide"`, plus `passes`, `rescue_passes` and `row_bytes`).
- **Exit codes:** 0 is a full matching, 4 incomplete (no payload), 1 an error.
- **The payload must not exist beforehand,** and it is removed unless the matching completes.
- **p = 2 is refused:** F is then a power of two, so the payload has no spare value to mark a
  request "unmatched".
- **`kh_verify_wide` takes `kh_verify_khm1`'s arguments,** plus `--threads` and `--row-bytes`.

## How it works

1. **Count walk:** one walk over all q labels records, for every thread's chunk of labels, how
   many fall in each cell. This fixes where each chunk's labels go in every row.
   (`src/field_walk.c`: `fw_walk_prepare`.)
2. **Layout:** the used cells are cut into blocks of about equal GPU cost (rows, plus requests).
   - **Sparse blocks:** a block whose requests average fewer than 4 neighbours in a window of its
     own size (F·m/q < 4; `--sparse-density`) gets no window and never runs. 5¹⁵'s tail, F cells
     of 3 requests each, is the case: about 0.14 neighbours a request.
   - Their requests start out pending. Each pass carries a slice of their cells, proportional to
     its dense blocks, and those blocks import them in their first run, into windows enlarged by
     exactly as many rights. Blocks get more import cells to fit (`import_cells` in the log).
   - Fields without sparse blocks lay out exactly as before. The full story is in
     [docs/WIDE_MATCHING_SPARSE_TAIL.md](../docs/WIDE_MATCHING_SPARSE_TAIL.md).
3. **Passes:** consecutive blocks are grouped so their rows fit `--row-bytes`. Each pass is one
   placement walk for its cells (`fw_walk_rows`), plus the cells of requests carried in from
   earlier passes.
   - It runs each of its blocks once, then exchange rounds among them.
   - Spot checks run on every pass: label counts per chunk, and sampled labels against
     independently computed powers.
4. **Choices** go straight to their place in the payload, at the certificate's width
   (17 bits for 7¹³).
   - All ones means unmatched.
   - After each block, its runs are written with `pwrite`, with an `fdatasync` every 64 MB, so
     dirty pages can't pile up behind the leader's fsyncs on merlin.
   - Exchange rounds read them back with `pread`.
5. **Rescue passes:** requests still unmatched after the last pass get passes that rebuild the
   rows of blocks with free rights, and run more exchange rounds.
6. **Final check:** the whole payload is read back. Every choice must be below F, and the padding
   must be zero.

**Not checked in the solver:** the block solver's final "each right endpoint once" bitmap (q/8,
12 GB for 7¹³) needs every row at once. Uniqueness holds by construction (disjoint windows, each
block's device self-check), and `kh_verify_wide` checks every endpoint.

**Inherited limit:** when n = q, every block's window is exactly its own size. A block's leftover
requests then have nowhere to go, since every other window is full, and the run ends incomplete
(exit 4). The block solver behaves the same way. Small blocks show it: 7⁵ in blocks of 3,000
leaves 9. Production blocks (about 10⁸ requests on the 3060) had a round-1 residual of 0 on
11⁹, 13⁹, 29⁷ and 31⁷. Leftovers come from blocks with few neighbours per request in their window,
which is why sparse blocks are handled separately (above).

## Measured (merlin, 2026-10-06, alongside DP tiles)

| What | Result |
|---|---|
| Count walk at r = 13 (5¹³, 8 threads) | 2.6 × 10⁸ labels/s; 3.0 × 10⁸ on 14 threads |
| Placement walk, 0.6 GB pass | 1.7 × 10⁸ labels/s |
| Placement walk, 4.9 GB pass | 7.4 × 10⁷ labels/s on 8 threads, 8.3 × 10⁷ on 14 (random writes) |
| 13⁷ (q = 6.3 × 10⁷), 9 blocks | block solver 6.8 s; wide solver 29 s in 9 forced passes; verified by both verifiers (`kh_verify_khm1` 12 s, `kh_verify_wide` 3.1 s on 8 threads) |

For 7¹³ (q = 9.7 × 10¹⁰, 166 GB of used rows) this predicts:
- **Count walk:** about 6 minutes.
- **Placement:** 9 passes of 20 GB, each about 6 minutes of walking plus 3–20 minutes of
  random writes, so **about 1–4 hours of field building** in total.
- **Matching:** about 1.5 hours for the blocks.

## Tests

- **`tests/field_walk_unit.c`:** rows from passes over random cell sets match `dp_solver/src/field.c`
  on 18 small fields, with 1–4 threads and narrow label words. The 7¹³ and 13⁹ polynomials are
  primitive.
- **`tests/check.py --run`:** solver checks.
  - Six real-field fixtures through the KHM1 verifier, in one pass and in several, with narrow
    label words.
  - Synthetic graphs: a per-block maximum-matching oracle, exchange after pass 1, rescue passes,
    and incomplete runs that leave no payload.
  - Default 60 graphs; `--seed 7 --cases 240` also passes.
- **`tests/check_verify.py`:** `kh_verify_wide` against `kh_verify_khm1`.
  - Archived campaign certificates (5⁹, 19⁵, 23⁵, 3¹⁵, 29⁵, 11⁷), plus two from this solver.
  - Each in 1–4 threads, one pass or many, and narrow label words.
  - Corruptions (a changed choice, a choice out of range, nonzero padding) are rejected by both
    verifiers.
- **`tests/bench_walk P R THREADS [EIGHTHS]`:** times the two walks on a real field (CPU and RAM only).

## Files

| Path | Purpose |
|---|---|
| `src/main.c` | `kh_gpu_wide_kernel`: layout, passes, block runs, exchange, in-place payload |
| `src/field_walk.c`, `src/field_walk.h` | 64-bit field arithmetic, the count walk and per-pass placement walks |
| `src/kernels.cu`, `src/kernels_images.c` | CUDA kernels (32-bit choices, binary-searched high part) and committed NVRTC images |
| `src/verify_wide.c` | `kh_verify_wide`: independent multi-pass, multi-threaded verifier |
| `adapter.py`, `cluster_solver.py`, `submit.py` | `match_gpu_wide` adapter, bridge (in-place publication), specification builder |
| `tests/` | the checks above, and `check_cluster.py` (end to end) |
