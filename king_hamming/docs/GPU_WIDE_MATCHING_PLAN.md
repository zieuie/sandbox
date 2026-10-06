# Plan: a wide matching solver for 7¹³ and larger fields

Status (2026-10-06): **solver and verifier built and tested** in
[gpu_wide_match_solver/](../gpu_wide_match_solver/README.md). They are not wired into the campaign
(section 7), and a full-width rehearsal is still to do (section 8). Disk is still the blocker for
7¹³ itself (section 6). Designed on 2026-10-05; built the next day as planned, with CPU multi-pass
rows, not GPU-built rows.

## Why a separate solver

Today's block matcher (`gpu_block_match_solver`, program `match_gpu_blocks`) matched 13⁹ and
29⁷. It refuses 7¹³ for three reasons:

| Limit | 7¹³ needs | Block matcher today |
|---|---|---|
| Choices per cell (F) | F = 7⁶ = 117,649 | F ≤ 65,534 (16-bit choices) |
| Field size (q) | q = 7¹³ = 9.69 × 10¹⁰ | q < 2³⁶ = 6.87 × 10¹⁰ |
| Host RAM for field rows | about 166 GB (4 bytes × F × 3F used cells) | built in one go; merlin has 38 GB |

The fixes make the solver slower on fields that already fit, so they go in a **new folder,
`gpu_wide_match_solver/`**, copied from the block matcher. The existing solver stays the
production path, and the wide one runs only on fields the block matcher refuses:

- **Wider choices** cost each request 4 more bytes on the GPU (about 35 instead of 31), so blocks
  hold about 10% fewer requests.
- **Multi-pass rows** walk the whole field once per pass instead of twice in total.
- **Choices written in place** turn one sequential write into many scattered ones.

On 13⁹ these might add up to 20–50% more run time, for no benefit.

The native verifier (`matching_solver/kh_verify_khm1`) hits the same three limits, and it is
single-threaded. It needs a wide counterpart as well (section 5).

## 7¹³ in numbers

| Quantity | Value |
|---|---|
| q, F, cells (budget) | 96,889,010,407; 117,649; 823,543 |
| Used cells (a_max = 3) | 352,947 |
| Requests n | about 9.7 × 10¹⁰ |
| Choice width in the certificate | 17 bits |
| Certificate | about 206 GB |
| Breakpoints per row (label high parts above 2³²) | 22 |
| Rows per cell | 470 KB (4 bytes × F) |
| Requests per used cell | about 275,000 |
| Blocks on the 3060 (6 GB) | about 590 blocks of about 1.6 × 10⁸ requests, about 600 cells each |

## 1. Wider choices

**On the GPU:** `choice`, `viak` and `end_k` become 32-bit, and `NOCHOICE` becomes `0xFFFFFFFF`.
The kernels have no other 16-bit assumptions. `block_cost()` (C and `adapter.py`) moves from 31 to
about 35 bytes per request.

**On the host:** the block matcher keeps 2 bytes per request in a scratch file, then writes the
packed certificate at the end. At 7¹³ that's 194 GB of scratch, and 388 GB if widened to 4 bytes.
Neither fits. Instead, **choices live in the payload file itself**, packed at the certificate's
width (17 bits):

- The output is created and sized up front as a sparse file. After each block runs, its choices
  go straight to their final canonical positions with `pwrite`.
- A block's requests are one run of consecutive cells per (DP run, coset). At 7¹³ that's about
  270,000 runs of about 1.3 KB per block, about 750 KB apart.
- An unmatched request is stored as all ones (`2^bits − 1`). That value can't be a real choice
  unless F is a power of two, which only happens for p = 2. The wide solver refuses p = 2 and
  leaves those fields to the block matcher.
- Exchange rounds read choices back with `pread`: the target block's own runs, plus single
  entries for its imports.
- There is no separate write stage. A final read of the payload confirms there's no sentinel left
  and every choice is below F.

**I/O cost.** Each 1.3 KB run dirties one or two 4 KB pages, and neighbouring blocks rewrite the
same pages. I estimate 3–6 times the certificate in actual writes, so 0.6–1.3 TB. That's about
0.1–0.2% of a typical NVMe drive's rated endurance. To keep merlin's dirty pages small (the 13⁹
write storm stalled the leader's fsyncs), the solver calls `fdatasync` after about every 64 MB.

**Lower-I/O alternative, not chosen:** buffer a whole pass's choices in RAM and flush them in long
runs. At 7¹³ that's about 26 GB per pass on top of the rows, so it would double the number of
passes. Walks cost more than writes, so this loses.

## 2. Lifting the q cap

- **Limits:** `FP_MAX_Q` goes to 2⁴⁰, and `FP_MAX_BREAKPOINTS` to 256.
- **High part of a label:** today `label()` counts breakpoints linearly, which is fine at 2 or 3
  breakpoints but not at 22. It switches to a binary search over the row's breakpoints, on the
  GPU and on the host.
- **32-bit audit:** these stay 32-bit, and each needs a check with a clear error.
  - cosets, which are at most the budget, 823,543 at 7¹³;
  - a block's window size and its local request indices, which are under 2³² by
    construction (about 1.6 × 10⁸);
  - the number of blocks.
- **Dropped:** the block matcher's final host-side "every right endpoint once" bitmap. It needs
  q/8 bytes (12 GB at 7¹³, 137 GB at 2⁴⁰) and every row at once. Uniqueness still holds by
  construction (block windows are disjoint, and each block's device self-check verifies its
  window). The independent verifier then checks every endpoint.

## 3. Multi-pass field rows

The field rows don't need to be in memory all at once. A block reads only its own cells' rows,
plus up to 256 extra rows for imported requests.

1. **One count walk.** It records, for each thread's chunk of labels, how many labels fall in
   each cell. That's 4 bytes × chunks × budget, about 100 MB at 7¹³. These offsets make every
   later pass deterministic and parallel, as in `fp_build_rows`.
2. **Passes.** Consecutive blocks are grouped so that a pass's rows fit a `--row-bytes` budget,
   about 20 GB on merlin, which gives 8–9 passes at 7¹³.
   - Each pass is one placement walk over all q labels. It keeps only the cells in the pass's
     set, using a cell-to-slot map (3.3 MB).
   - The set is the pass's own cells, plus the cells of any requests carried in from earlier
     passes.
   - The spot check (labels against independently computed powers) runs on every pass.
3. **Matching.** Round 1 runs on each block of the pass, then exchange rounds run among those
   blocks.
4. **Leftover requests** are carried into the next pass, whose walk also builds their cells' rows,
   so they can be imported there. Blocks that already hold imports get those imports' rows the
   same way.
5. **Rescue passes.** If requests remain after the last pass, these passes rebuild the rows of
   blocks that still have free rights, plus the pending cells, and run more exchange rounds. A
   limit on the number of rescue passes ends a hopeless run as "incomplete" (exit 4), as today.

Every field so far had a round-1 residual of 0, including 13⁹, so exchange is expected to be rare.

**Time estimate for 7¹³ on merlin**, from the built solver's measurements (2026-10-06):

| Stage | Estimate | Based on |
|---|---|---|
| Count walk | **about 6 min** | 2.6 × 10⁸ labels/s at r = 13 (5¹³, 8 threads) |
| Placement: 9 passes of about 20 GB | 6 min of walking plus 3–20 min of random writes each, so **about 1–4 h** | 1.7 × 10⁸ labels/s on a 0.6 GB pass, 7.4 × 10⁷ on a 4.9 GB pass |
| Matching: about 590 blocks | about 9 s each, so **about 1.5 h** | 11⁹: 16 blocks in 147 s |
| Choice writes | interleaved; tens of minutes in total | 2–4 GB of page writes per block |
| **Solver total** | **about 3–6 h** | was 7–9 h when estimated from the old builder |

The rescue passes described above are built, as is refusing p = 2 (F is a power of two, so the
payload has no spare "unmatched" value). **One inherited limit:** when n = q, a block's leftover
requests have nowhere to go, because every other window is exactly full. Exchange can't fix that,
in either solver. Production-size blocks have never left any (11⁹, 13⁹, 29⁷).

**Faster option for later:** build each block's rows on the GPU. A walk over 9.7 × 10¹⁰ labels
should take a few seconds on the 3060, so 590 blocks × 2 walks is about 30 minutes, with no rows in
host RAM. It needs new CUDA field arithmetic with its own checks, so it's a phase-2 speed-up,
not the first version.

## 4. Where it runs

- **merlin** is the only host with a 6 GB GPU and enough disk. It has 16 threads and 38 GB of RAM;
  the leader and dashboard run there too.
- **While the run holds merlin's GPU:** DP tiles already stay off it (the `gpus_all_leased` rule).
- **Host RAM:** about 20 GB of rows per pass, 100 MB of offsets, and per-block staging (about
  0.7 GB: 32-bit choices of one block).
- **The lease** declares `max_bytes` from `host_bytes()`, as the block matcher does.

## 5. A wide verifier (`kh_verify_wide`)

`kh_verify_khm1` builds all used rows in RAM (166 GB at 7¹³), caps q at 2³⁶, and walks the field
on one thread. 29⁷'s check took about 2 hours. It runs twice per matching: on the worker, and
again in the feeder.

The new verifier keeps the same command line (`P R POLY BLOCKS FILE OFFSET`), adds `--threads`
and `--row-bytes`, and stays **independent of the solver**:
- **No shared code with the solver.** It shares nothing with `field_prefix.c` or `field.c` and
  mirrors the Python verifier's packed-integer arithmetic. It uses a precomputed-reciprocal
  `mod p` so the per-digit divisions get cheap.
- **Multi-threaded.** Each thread's chunk starts at X^(z−1), computed by its own exponentiation.
  One count walk gives each chunk its offset in every cell.
- **One pass per cell range.** It builds that range's rows, then reads only the matching runs of
  the certificate with `pread`. It marks every right endpoint in a q/8 bitmap (12 GB at 7¹³) and
  fails on a repeat, a choice out of range, or nonzero padding.
- **Measured:** 13⁹ (q = 1.06 × 10¹⁰, 19.9 GB certificate) verified in **7 min 9 s** on 10
  threads in 4 passes, peaking at 5.5 GB of memory. `kh_verify_khm1` took about 2 hours and
  16.5 GiB.
- **Scaled to 7¹³:** about 10 times the rows and 9 times the labels, so **about 1–2 hours** with
  12–20 GB passes, plus the 12 GB bitmap. That's twice (worker and feeder) unless one check is
  dropped.
- **Possible saving:** skip the worker's check for this program and rely on the feeder's.

## 6. Disk: the real blocker

A 7¹³ certificate is about 206 GB. Today the result is copied up to three times:
- the solver's payload is wrapped into the KHM1 file (`artifacts.publish` copies it);
- the agent copies that file into the blob store;
- the feeder copies it into `matching-results/`.

29⁷ had three 32 GB copies on disk at once this evening. merlin has **146 GB free** of 461 GB.

Needed before a 7¹³ run:
1. **Write the payload inside the final file.** Reserve the KHM1 header (its length is known in
   advance), then hash and append the checksum. That takes out one copy.
2. **Hard-link instead of copying** into the blob store and the archive, which are on the same
   filesystem.
3. **Free space or add a drive.** Even with one copy, 206 GB is more than merlin has free. Plus
   the 7¹³ DP artifact.

## 7. Integration (when it's built)

- **Program `match_gpu_wide`:** an adapter, bridge and spec builder copied from the block
  matcher, registered in the leader's adapters, and with its own worker rollout.
- **Feeder routing:** use `match_gpu_wide` only when `match_gpu_blocks` is refused (F > 65,534,
  q ≥ 2³⁶, or rows over the host-RAM limit), and not for p = 2.
- **Verifier:** `artifacts.verify` picks `kh_verify_wide` for fields `kh_verify_khm1` can't take.
- **Dashboard:** the feeder note for a refused field lists every limit it hits, not just the first.
  That was offered separately and is still useful.

## 8. Tests

- **Small fields** with forced tiny passes (`--row-bytes`) and narrow label words, so many passes,
  carried-over requests and many breakpoints all happen on fields that run in seconds.
- **Exchange across passes:** the block matcher's synthetic deficient graphs, with blocks split
  across passes.
- **In-place payload:** byte-identical to the block matcher's payload on the same field and
  layout. A killed run leaves no partial certificate.
- **Wide verifier:** agrees with the Python verifier and with `kh_verify_khm1` on good
  certificates, rejects corruptions, and gives the same answer with 1 thread and 12, and with 1
  pass and many.
- **Full-width rehearsal (still to do):** the smallest field with F > 65,534 and an odd exponent
  is **5¹⁵** (q = 3.05 × 10¹⁰, F = 78,125, 17-bit choices, 7 breakpoints per row). It needs no DP:
  the blocks file `1` / `5 78125` asks for every cell of every coset, so n = q.
  - **Cost:** about 122 GB of rows in several passes, about 190 blocks (roughly 30–60 minutes of
    merlin's GPU), and a 65 GB payload.
  - **So** it needs a planned window: after 31⁷'s matching, with DP tiles off merlin's GPU, and
    disk to spare.
- **Done (2026-10-06):**
  - `tests/field_walk_unit.c`, `tests/check.py` (60 cases, plus 240 with another seed) and
    `tests/check_verify.py` (8 certificates, corruptions) all pass.
  - 13⁷ ran through the wide solver in 9 forced passes and was verified by both verifiers.
  - 13⁹'s archived certificate verified with `kh_verify_wide`.

## 9. Effort and open questions

- **Effort:**
  - solver and wide verifier: done;
  - disk plumbing: about half a day;
  - the 5¹⁵ rehearsal, cluster integration and rollout: about 1 day.
- **Open questions:**
  1. Is 7¹³ worth about 3–6 h of merlin's GPU plus 2–4 h of verification, given the disk it needs?
  2. Add a drive to merlin, or free space? (The hardware brief compares options.)
  3. ~~CPU multi-pass or GPU-built rows?~~ CPU multi-pass was built; measured, it's fast enough
     that GPU-built rows can wait.
