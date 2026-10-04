# Matching fields larger than one GPU: block decomposition

Design, 2026-10-02. Stage 1 is implemented (see the solver's README for results). Stage 2's
prefix-only builder was implemented on 2026-10-04 together with 64-bit labels, for 13^9
(see [MATCHING_13_9.md](MATCHING_13_9.md)); it lives in `gpu_block_match_solver/src/field_prefix.c`,
not in `kh_build_field`. It is a separate solver, `gpu_block_match_solver/`
([README](../gpu_block_match_solver/README.md)), built beside `match_gpu`
([gpu_match_solver](../gpu_match_solver/README.md)) for fields that are larger than
any single GPU's memory. Fields that fit one GPU keep using `match_gpu`, which can
also prove that no perfect matching exists. Five saved fields are in that position
today: 17^7, 2^29, 19^7, 7^11 and 11^9.

**In short:** split each field into blocks. A block pairs a set of requests with
a matching-sized range of right vertices, and fits on one GPU. Match every block
on its own with the existing kernels. Then hand the few requests left unmatched
to other blocks that still have free right vertices, for a few rounds, until
none are left. One process on merlin runs the blocks one after another. A
version that spreads the blocks over all ten GPUs is described at the end, but
it is not recommended yet.

## 1. Why not a GPU port of `matching_solver_multi`

`matching_solver_multi` gives each machine a slice of the right vertices and
sends every discovery to the slice's owner over the network. Ported to GPUs,
every neighbor a GPU scans would turn into a network message, so the GPUs would
spend most of their time waiting:

- **The network is shared Wi-Fi** ([CLUSTER_INVENTORY.md](CLUSTER_INVENTORY.md)).
  All ten machines share one radio channel.
- **The volume is large.** One exact BFS of the partitioned design moves up to
  `machines × q` discovery records
  ([PARTITIONED_MATCHING.md §4](PARTITIONED_MATCHING.md)). For 11^9, with ten
  machines and 12-byte records, that is about 280 GB per BFS. Even if the
  per-BFS volume falls to about `q` records (about 28 GB), that is minutes to
  hours of Wi-Fi per phase.
- **The CPU prototype was already limited by its exchange step.** At 13^5 it
  spent 125 s of a 159 s search in exchange calls
  ([EXPERIMENTS.md](../matching_solver_multi/EXPERIMENTS.md)). A GPU makes the
  compute part about 100× shorter and leaves the exchange as it is.

**GPU memory is the limit, not GPU speed.** On merlin's 3060, a field that fits
takes 1–4 s to solve. The fix is to cut the problem into pieces that each fit.
Once the pieces are mostly independent, they don't have to run at the same time
or on different machines.

## 2. Facts this design relies on

Measured from the saved KHD1 inputs (`results/*.khdp`):

| Field | q = n (requests) | F | Python verifier needs | Rows of used cells | Choices (2n) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 17^7 | 410 M | 4913 | 10 GiB | 0.4 GiB | 0.8 GiB |
| 2^29 | 537 M | 16384 | 12 GiB | 2.0 GiB | 1.0 GiB |
| 19^7 | 894 M | 6859 | 20 GiB | 0.9 GiB | 1.7 GiB |
| 7^11 | 1,977 M | 16807 | **45 GiB** | 3.2 GiB | 3.7 GiB |
| 11^9 | 2,358 M | 14641 | **53 GiB** | 3.2 GiB | 4.4 GiB |

- **The request count equals q in every one of these fields.** A complete result
  is therefore a perfect matching, and every right vertex is used.
- **Requests use only cells with prefix < a.** Request `(coset, cell)` uses a
  cell `prefix*F + suffix` with `prefix < a` for its DP block. With
  `a_max = max a`, the used cells are `0 .. a_max*F-1`, and the rest of the
  `4q`-byte cell table is never read. That is why the table above shows so little
  for "rows of used cells".
- **The Python KHM1 verifier needs more memory than any machine has (merlin:
  38.9 GiB) for 7^11 and 11^9.** At about 4 µs per label it would also take
  2–3 hours. A native streaming verifier is part of this design (stage 3).
- **The neighbors are spread evenly.** For request `(cell, c)`, neighbor k is
  `v = 1 + ((z_k − 1 − c) mod (q − 1))`, where `z_k` is the k-th label in the
  cell's ascending row; `z = 0` gives `v = 0`. If the rights are cut into P
  contiguous ranges, each request should have about `F/P` neighbors in every
  range. That is the expected count; the measured minimum is part of stage 1.

## 3. Block layout

Choose a block budget `B` in device bytes: the free memory on the leased device,
less a margin. The layout depends only on `(DP, B)`, so it is deterministic.

- **Requests (left side).** Block b owns a contiguous **cell** range
  `[c_lo, c_hi)` within `[0, a_max*F)`, and every request whose cell falls in it.
  For each DP block j (blocks-file line `(a_j, copies_j)`) that contributes the
  rectangle `copies_j cosets × [c_lo, min(c_hi, a_j*F))`. A request is never
  split from its cell, so a block uploads only its own rows:
  `4*F*(c_hi − c_lo)` bytes.
- **Rights.** Write `m_b` for the number of requests in block b. Block b owns
  right IDs `R_b = [Σ_{b'<b} m_b', Σ_{b'≤b} m_b')`. The ranges tile `[0, n)`
  exactly. If `n < q` ever occurs, the last block's range also takes
  `[n, q)`, which only adds spare capacity.
- **Cutting.** Walk the cells in order and add cells to the current block while
  `4*F*cells + 31*m_b + import_reserve + 16 MiB ≤ B`. Here 31 B per request is
  the current kernel's per-request state; see §6. A single cell that does not
  fit is an error.
- **Local request order** is like the existing `graph_t`: by DP block, then by
  coset, then by cell. `decode(u_local)` binary-searches the clipped DP blocks
  and returns `coset = bcoset_j + off / w'_j` and
  `cell = c_lo + off % w'_j`, with `w'_j = min(c_hi, a_j*F) − c_lo`. This is the
  existing `decode` plus a `cell_base` field.
- **Global ID:** `u = bfirst_j + (coset − bcoset_j)*a_j*F + cell`, the same
  numbering as KHM1. The host converts in both directions.

Projected layouts:

| Field | Blocks on merlin (3060, ~5.4 GiB budget) | Expected in-block degree | Blocks on a P600 (~1.6 GiB) | Expected in-block degree |
| --- | ---: | ---: | ---: | ---: |
| 17^7 | 3 | ~2,200 | 8 | ~640 |
| 2^29 | 4 | ~5,200 | 12 | ~1,500 |
| 19^7 | 5 | ~1,400 | 17 | ~410 |
| 7^11 | 12 | ~1,500 | 39 | ~450 |
| 11^9 | 14 | ~1,100 | 46 | ~330 |

These are projections from the formulas, not measurements.

## 4. Scanning only a block's neighbors (the window)

A block needs only the neighbors that fall in its own right range, and those
form one or two contiguous runs of the request's sorted cell row. So the kernel
scans them directly and skips the others; nothing is filtered after the fact.

For request `(cell, c)` and block range `[lo, hi)`:

1. **Zero.** If the row's entry 0 is label 0 (cell 0 only) and `lo == 0`, then
   `k = 0` is in the window.
2. **Nonzero labels.** Let `t0 = max(lo, 1) − 1` and `t1 = hi − 1`, the
   exponents of the target rights, with `L = t1 − t0`. The source exponents are
   `e = (t + c) mod (q−1)` for t in `[t0, t1)`, which is the circular interval
   starting at `s = (t0 + c) mod (q−1)` with length L. The labels are
   `z = e + 1`.
   - If `s + L ≤ q − 1`, it is one label interval, `[s+1, s+L+1)`.
   - Otherwise it is two: `[s+1, q)` and `[1, s+L−(q−1)+1)`.
3. **Index ranges.** Use `lower_bound` on the row (skipping entry 0 when it is
   label 0) to turn each label interval into an index range `[k_a, k_b)`.
4. **Choices.** A kernel's position in the window maps back to the row index k,
   and **choices stay global row indices**. The KHM1 payload is unchanged.

A CPU reference function (`window_ranges`) and a brute-force test are required.
The test enumerates all k, filters by `lo ≤ v < hi`, and must give the same set
as the window, on random small fields, cosets, ranges, and the `lo = 0`,
`hi = q` and wraparound cases.

## 5. The algorithm

The host holds the authoritative global state:

- `choice[n]` (u16). `0xFFFF` means unmatched. F ≤ 65534 is required, so the
  sentinel can never be a real choice; F = 65535 must be refused in block mode.
- For each block, a list of the requests imported into it and matched there.
- The used-cell rows, built once on the CPU (§6).

**Active requests in block b** are the requests b's search may use:

- own requests whose current right `v(u)` is in `R_b`;
- requests imported into b in earlier rounds and matched there;
- this round's new imports.

Every other request of b is *inactive*: an own request that is free, or one
matched in another block's range. Inactive requests are never roots and never
on a path.

**Round 1.** For each block in turn:

1. Upload its rows, the layout metadata, and the right slice (all `FREE`).
2. Run greedy (with the hashed start offset inside the window), then APFB phases
   until a phase claims nothing.
3. Run the existing `check` kernel, adapted to the window.
4. Download the choices into `choice[]`.

**Round t ≥ 2: exchange.**

1. Collect the free requests U, and each block's free-right capacity
   `cap_b = |R_b| − (requests matched into R_b)`. When n = q, `Σ cap_b = |U|`
   exactly.
2. Assign each `u ∈ U` (in ascending order) to a block with `cap_b > 0` that it
   has not tried before, in the rotating order `(home(u) + t + j) mod P`. Decrease
   `cap_b`. Keep a `tried` bitmask of at most 64 bits per u while U is small.
3. For each block that received imports:
   - Upload the block with the right slice rebuilt from active matched requests.
     Build it on the host from `choice[]` and the import lists, or with a
     `rebuild_right` kernel. A duplicate right is an internal error.
   - Mark its own free requests INACTIVE (`0xFFFFFFFD` in `left[]`).
   - Append the imports as extra left vertices. They come after the `m_b` own
     requests, with explicit `(coset, row slot)` arrays, and the rows of their
     cells are uploaded after the block's rows.
   - Run greedy and APFB again. Only `FREE` (not INACTIVE) requests become roots,
     so the roots are exactly the imports.
   - Write back the choices and add newly matched imports to the block's import
     list.

**Stopping.**

- U becomes empty: write the KHM1 full-matching payload. Exit 0.
- A round augments nothing, `--max-rounds` is reached (default 16), or the
  round-1 residual exceeds `--max-residual` (default `n/1000`): exit **4**,
  *incomplete*. Write no payload, and record the residual in the metadata.

**Why the result is a valid matching:**

- Each block's search only assigns rights in its own `R_b`, and the `R_b` are
  disjoint.
- A request is active in at most one block at a time. If it is matched, that is
  the block whose range holds its right; if it is free, it is either imported
  into exactly one block this round or inactive everywhere.
- An augmenting path flips only active vertices of one block, and never
  unmatches a request.

So `choice[]` always describes a matching of the whole graph, and when U is
empty it is perfect. The existing verifier checks that independently. Note that
the exchange is a heuristic and does not prove maximality.

**What this design does not do:** prove that no perfect matching exists. Block
mode never exits 2 and never writes a Hall certificate. A true Hall obstruction,
or bad luck, ends as exit 4. The bridge reports exit 4 as a failed attempt
(`block matching incomplete: residual R after T rounds`), never as an
obstruction.

**Expected behavior (a hypothesis that stage 1 tests):** with in-block degrees of
several hundred or more, each block should match perfectly or almost perfectly
on its own, as the whole field does today in 1–2 APFB phases. The exchange
rounds should then be short. If that turns out to be false, this design is not
enough on its own; see stage 1's gate.

## 6. Memory

- **Device, per block.** `4*F*slots` for rows, plus `31*(m_b + imports)`, plus
  `4*|R_b|`, plus 16 MiB. `slots` counts the block's cells plus its unique
  import cells. Breakdown of the 31 B per request: left 4, choice 2, root 4,
  parent 4, viak 2, two queues 8, per-root state about 2.25 (budgeted at m/8 as
  today), plus the right slice. Allocate once at the largest block's size and
  reuse it for every block. Cap imports at `m_b/64` per block per round.
- **Host.**
  - Rows of the used cells only: `4*F*a_max*F`. Add
    `kh_build_field_prefix(..., cell_limit)`, which counts every cell (the
    positions are `4*budget*threads`, as today) but stores only the cells below
    `cell_limit`. The existing `kh_build_field` stays unchanged.
  - `choice[]`: `2n`.
  - Import lists: small.
  - Staging buffers: `6*m_max`.
  - 64 MiB overhead.

  For 11^9 that is about 8 GiB, against today's single-GPU formula
  `4q + 4n = 17.6 GiB`.
- **The device must be locked for the whole run.** The bridge already holds
  `DeviceLock` while the kernel runs. A block-mode run takes minutes, not
  seconds, so DP tiles on that host stop waiting after
  `KH_GPU_DP_WAIT_SECONDS` (120 s) and fall back to CPU. That is acceptable.

## 7. Command-line contract (additions to `kh_gpu_match_kernel`)

```
--blocks off|auto|force   auto (default): block mode only when the whole field exceeds the device budget
--block-device-bytes N    block budget B; default: free device memory minus 256 MiB, capped by --max-device-bytes
--block-requests N        tests only: upper bound on m_b, to force many small blocks on small fields
--max-rounds N            default 16
--max-residual N          default n/1000; a larger round-1 residual stops with exit 4
```

**Exit codes.** 0 full matching; 2 obstruction (single-GPU mode only); 4
incomplete (block mode only); 1 error.

**Metadata JSON.** The existing fields plus:

- `"engine":"gpu-blocks"`
- `"blocks":P`
- `"rounds":T`
- `"residual_round1":R1`
- `"residual":R`
- a per-round list: `round`, `imports`, `matched`, `seconds`
- `"min_window_degree"` (cheap to compute while uploading)

`"seconds"` gains `"blocks"` and `"exchange"`.

**Progress lines** use the existing shape. `phase` is `"field"`, then
`"block 7/14"`, `"round 2: 31 imports"` and `"writing"`. The dashboard needs
nothing new to show a progress bar.

## 8. Implementation stages

Each stage ends with its tests passing and docs updated. Build kernels with
`make -C king_hamming/cuda toolchain && make -C king_hamming/gpu_match_solver images`
after editing `kernels.cu`; the NVRTC images are committed.

### Stage 1: block mode in the kernel, measured on fields with known answers

- Add the window (with the CPU reference and test), the block layout, `decode`
  with `cell_base` and imports, window-aware `greedy`/`expand`/`check`,
  INACTIVE, the host loop with rounds, and exit 4.
- Tests in `tests/check.py`:
  - Every fixture in `--blocks force` at several `--block-requests` sizes
    (forcing 2, 8, 32 and 128 blocks) gives a KHM1 that the existing Python
    verifier accepts.
  - A synthetic deficient `--test-cells` graph in block mode exits 4, never 2.
  - The single-GPU path still gives the same exit codes and verified results on
    all fixtures.
- Measure, and save the results to `results/blocks.jsonl` and a README table: for
  2^23, 5^11, 13^7, 3^17 and 2^27 at P = 2, 4, 8, 16, 32 and 64, record the
  round-1 residual, the rounds to zero, the minimum window degree, and the time.
  These fields fit on merlin's GPU, so their full matchings are known to exist.
- **Gate: stop and report to Zooey before stage 2** if any of these fields fails
  to reach a full matching at P ≤ 64, or if a round-1 residual is above 0.1%.

### Stage 2: large fields on merlin

- Add `kh_build_field_prefix` and use it in block mode.
- Add `device_bytes_block`/`host_bytes_block` to `gpu_match_solver/adapter.py`,
  mirroring `main.c` as `device_bytes` does today.
- **Ask Zooey before running these.** Run 17^7, 2^29 and 19^7 manually on merlin
  with their pinned polynomials (the same as their DP rows, or the first
  candidate). Verify with the existing Python verifier, which fits on merlin for
  these three. Record the runs in the README results table.

### Stage 3: a native streaming verifier (implemented 2026-10-02)

`matching_solver/kh_verify_khm1`, with `artifacts.verify(..., native=None|True|False)` as its wrapper;
status 0 only (obstructions stay on the Python path). Python still checks the header, DP digest,
checksum, polynomial and payload length. The differential test
`matching_solver/tests/check_native_verify.py` agrees with the Python verifier on 7 real fixtures
(odd and binary fields) and on over 1,800 corrupted files. Speed on 2^23: 0.2 s against 31 s.
Wiring: it is used automatically from 2^24 labels up, and `block_adapter.host_bytes` no longer reserves
24 bytes per label for verification. The original plan:

`matching_solver/kh_verify_khm1` (C). It must be written **independently of the
solvers**. It must not include `dp_solver/src/field.c`, `kh_field.h` or any GPU
code. Implement the field arithmetic, the request enumeration from KHD1 runs and
the packed reader from the format, as `artifacts.py` does.

**Checks for status 0 (required):**

- Every header check `artifacts.verify` does: magic, DP digest, p/r, a primitive
  polynomial, counts, bits, padding, and the trailing SHA-256.
- Build the used-cell rows.
- Stream the choices in canonical order. Each `k < F`; compute v; test-and-set
  a `q`-bit bitmap. A repeated v rejects the certificate.
- Require `matched == n`.

Memory is `4*F*a_max*F + q/8`.

Status 1 is optional; leave it to the Python verifier.

**Tests:** across every fixture and every certificate in `gpu_match_solver/results`
that fits, both verifiers must agree. They must also both reject corrupted
certificates: a flipped choice, a duplicated right, truncation, a wrong DP
digest, and nonzero padding.

**Wiring:** `MatchingAdapter.validate_result` and the bridge's restart check use
the native verifier when `24q + 512 MiB` exceeds the job's `max_bytes`, or when
q exceeds a setting (default 256 M). The GPU `host_bytes` uses the native
verifier's formula in that case.

**Then (ask first):** run 7^11 and 11^9 on merlin and verify them.

### Stage 4: campaign integration (implemented; not deployed)

Done 2026-10-02: program `match_gpu_blocks`, feeder tier, dashboard, tests. Differences from
the plan below: the setting `gpu_block_matching` defaults to **true** (Zooey asked for these
fields to be fed), the lease names the largest GPU explicitly, and an incomplete run moves
the feeder to the next polynomial instead of a salted retry. The original plan:

- **`campaigns/gpu_policy.py`.** Add a second tier: when no single GPU fits, but
  F ≤ 65534 and n < 2^32 − 1, plan `match_gpu` with the arguments
  `{"blocks": "auto"}`. Set `gpu_memory_bytes` to the largest usable GPU on a
  host with RAM ≥ `host_bytes_block`, so that the run lands on the biggest GPU.
  The kernel sizes its blocks from the device's actual free memory. Add a new
  pipeline setting `gpu_block_matching`, **default false**, for Zooey to turn on.
- **The adapter.** Pass the block arguments, use the block memory formulas, and
  give the block-mode estimate.
- **The bridge (`cluster_solver.py`).** Pass the arguments, map exit 4 to a
  failure with the residual in the message, and report the block metadata.
- **Feeder retries.** Check what the feeder does after a failed `match_gpu` run,
  and make sure it does not resubmit the same block-mode attempt in a loop. One
  retry with a different `--salt` is reasonable.
- **Dashboard (`web/`).** The Matching tab's engine column shows
  `GPU blocks · P blocks · T rounds`, and the waiting table explains block
  admission.
- **Docs:** GPU.md's "Feeder routing" section, the gpu_match_solver README, and
  web/README.
- **Tests:** in `cluster/tests/test_gpus.py`, routing, the setting off/on, and
  admission. Extend `gpu_match_solver/tests/check_cluster.py --run` with a forced
  small-block field.

### Stage 5 (optional, not recommended now): spread the blocks over the fleet

Only worth building if merlin's GPU becomes the bottleneck for the queue of
large fields. Each block would become a child run (`match_gpu_block`), like
DP tiles under `dp_distributed`:

- **Leader.** It keeps a block table and plans the exchange rounds in
  `advance()`.
- **Child jobs.** Each child builds only the rows it needs with a GPU field-scan
  kernel, since a full CPU field build on a P600 host would take about 15 min
  per block for 11^9. It uploads its state, 2 B per request, as an artifact.
- **Assembly.** It happens on merlin.

**Costs.** For 11^9 that is about 4.4 GiB of block state, plus replicas,
crossing Wi-Fi at least twice. The whole job is about one GPU-minute on merlin,
so this probably isn't faster; its benefit is not depending on one machine.

## 9. Risks and open questions

- **Local residuals (the main risk).** If blocks leave many requests unmatched,
  the exchange can stall, and the run ends as exit 4. Stage 1 measures this
  before anything large is built.
- **Proving that no perfect matching exists** is out of scope. A field whose
  attempts keep ending incomplete stays on its CPU path (`field limit`), as
  today.
- **merlin's CPU load.** A 2–5 minute CPU field build for 11^9 shares merlin
  with DP tiles. That is admitted as usual through `threads` and `max_bytes`.
- **Certificate size.** The 11^9 KHM1 payload is about 4.1 GB (14 bits per
  request), and is replicated like other results.
- **No checkpoints.** A lost lease reruns the whole attempt, which takes minutes.

## 10. Rules for the implementer

- Work only in `king_hamming/`. Never commit or push; Zooey commits.
- Do not start, stop, upgrade or deploy anything on the campaign: leader,
  agents, feeder or dashboard.
- Ask Zooey before any manual run on merlin's GPU or any run bigger than the
  fixtures.
- Keep the README and docs current at each stage. Report measurements as
  measured, labeled with field, P and device.
- `make -C king_hamming/gpu_match_solver check` and
  `make -C king_hamming/cluster check` must pass at the end of each stage.
