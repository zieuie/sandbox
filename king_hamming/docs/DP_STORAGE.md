# DP tile storage, tile limits, and the fields they block

Research of 2026-10-03 (night). It answers why the dashboard's *Submit field* command refuses
large fields, and what it would take to run them. Nothing here has been deployed; see
[Rollout](#rollout).

## Summary

1. **Tile values compress about 100× better than we store them.** DP values never decrease
   as either budget grows, neighbouring cells differ by at most a few units, and a tile uses
   only a handful of distinct choices. Today's packets are gzip-1 of the raw arrays,
   0.3–1.9 bytes per cell. A row-delta, byte-plane, xz encoding (tile format 2, implemented
   tonight, standard library only) stores **0.0024–0.021 bytes per cell** on real 13⁹, 23⁷, 37⁵
   and 5¹³ tiles, and is exact for any data. One real 13⁹ tile: 25.1 MB → 74 KB. Its bands:
   1 MB → 3 KB. Replication and halo traffic shrink by the same factor (9 of the 11 machines share
   the wired `kh-switch` network; dp-152 and dp-156 are on Wi-Fi only).
2. **The two refusal messages are separate limits, and neither is a hard limit:**
   - *2 GiB per tile* is each tile lease's memory reservation (`max_tile_bytes`). The C kernel
     uses `size_t` arithmetic and checked products, so larger tiles are fine. The agents report
     15.5 GiB on dp-101–108, 14.8 on dp-152, 7.4 on dp-156 and 38.9 on merlin. Raising it only
     lowers how many of that field's tiles share a machine. Large-prime cubes need it: 127³ needs 2.2 GiB; 199³ needs about 12 GiB.
   - *10,000 tiles* protects the leader. Every scheduler pass reads every tile of every waiting root
     inside the write lock. That cost caused this morning's lock storm (1.0 s per pass,
     fixed in `a8725ad` to 0.2 s for the 28,772 tiles of four roots). Above 10,000 tiles a
     root is now rescanned at a bounded rate, so the limit can be raised.
3. **The 1.37 PB example is 11¹³**, and storage is not its real problem. Its DP is about
   5×10¹⁷ raw visits. At the cluster's measured 2–7×10⁹ visits/s that is about 3 years.
   Many other blocked fields are cheap to compute, though: 3²⁵ (about 4 h), 7¹³ (about 13 h), 5¹⁷ and 3²⁷
   (1–2 days), 11¹¹ (about 10 days). With format 2 and raised limits they fit the disk we have.
4. **Keeping only the frontier ("diagonal") tiles** works for the forward computation but not,
   by itself, for reconstruction. Reconstruction needs the choices along the optimal path, which
   is unknown until the end. Keeping each tile's thin edge bands plus an exit pointer per band
   cell fixes that: the path's tiles can be found from bands alone and recomputed in parallel.
   A local prototype of this is exact against the dense solver ([below](#keeping-only-the-frontier)).
   After format 2 it matters only for the largest small-prime fields (3²⁹, 5¹⁹, 7¹⁵), so wiring
   it into the cluster is the next step, not tonight's.

## What the values look like

Measured on packets copied from merlin's blob store (`/tmp`, read-only; nothing on the cluster
was touched):

| tile | cells | monotone in u and v | largest step | distinct choices | stored now | format 2 |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| 13⁹ (12,9), side 4096 | 16.8 M | yes | 7 | 16 | 1.50 B/cell | 0.0044 |
| 23⁷ (32,32), side 4096 | 16.8 M | yes | 10 | 4 | 1.44 | 0.0034 |
| 37⁵ (6,6), side 512 | 0.26 M | yes | 26 | 39 | 1.80 | 0.021 |
| 5¹³ (16,16), side 1024 | 1.05 M | yes | 1 | 1 | 0.83 | 0.0024 |

Monotonicity follows from the recurrence: a cell's value is the best over transition paths
whose total cost fits its budget, and a larger budget admits every such path. Format 2 does
not rely on it. Each row is subtracted from the previous one as a single integer modulo
2^(64·width), which is exact for any bytes; monotone data just makes the differences tiny.

Codec choices, all measured on the 13⁹ tile (bytes per cell for values / choices):

| | values | choices |
| --- | ---: | ---: |
| gzip-1 of raw arrays (current) | 1.44 | 0.054 |
| xz preset 1, raw | 0.016 | 0.0017 |
| row delta + byte planes + xz preset 1, values; raw xz, choices (format 2) | 0.0027 | 0.0017 |
| byte planes for choices too | 0.0027 | 0.0026 |
| zstd -3 of narrowed column deltas / u16 choices | 0.0011 | 0.0017 |

The worker runtime depends only on the standard library (numpy is installed on dp-156 alone,
and no machine has a zstd module), so format 2 uses only `int.from_bytes`, strided `bytes` slices
and `lzma`. `lzma` round-trips, with identical output, on all 11 machines, including dp-152's
Python 3.14.4 (the others run 3.12.3). Byte planes help values on three of the four tiles and hurt choices
on all four, so choices are compressed unchanged. A 4096² tile encodes in about 1 s and decodes
in 0.3–0.5 s, against 30–160 s of computation. Cost grows linearly and memory does not grow. A
16384² tile (3 GiB raw, built from the 13⁹ tile repeated 4×4) encodes in 16.9 s and decodes in
7.7 s with 39 MB resident, round-trips exactly, and stores 694 KB.

## Fields, compute, and what blocks them

Raw visits are `B²p³`. Days assume 5×10⁹ raw visits/s, the order measured for 23⁷ over 40 h
(the per-lease rate is about 5×10⁸/s). Storage is three copies: *raw* is the feeder's current
uncompressed reservation, *format 2* is `scheduling.stored_bytes` (0.05 B/cell, about 2–20× above the
measurements, plus bands).

| field | budget B | halo p² | raw visits | compute | layout needs | side | tiles | raw ×3 GiB | format 2 ×3 GiB |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 3²⁵ | 1,594,323 | 9 | 6.9e13 | 0.2 d | max_tiles 40k | 8192 | 38,025 | 85,223 | 356 |
| 7¹³ | 823,543 | 49 | 2.3e14 | 0.5 d | max_tiles 40k | 8192 | 10,201 | 22,739 | 96 |
| 5¹⁷ | 1,953,125 | 25 | 4.8e14 | 1.1 d | 40k + 4 GiB | 16384 | 14,400 | 127,898 | 535 |
| 3²⁷ | 4,782,969 | 9 | 6.2e14 | 1.4 d | 100k + 4 GiB | 16384 | 85,264 | 767,005 | 3,199 |
| 11¹¹ | 1,771,561 | 121 | 4.2e15 | 9.7 d | 40k + 4 GiB | 16384 | 11,881 | 105,224 | 445 |
| 17⁹ | 1,419,857 | 289 | 9.9e15 | 23 d | max_tiles 40k | 8192 | 30,276 | 67,592 | 302 |
| 7¹⁵ | 5,764,801 | 49 | 1.1e16 | 26 d | 400k + 4 GiB | 16384 | 123,904 | 1,114,221 | 4,670 |
| 5¹⁹ | 9,765,625 | 25 | 1.2e16 | 28 d | 400k + 4 GiB | 16384 | 356,409 | 3,197,443 | 13,363 |
| 3²⁹ | 14,348,907 | 9 | 5.6e15 | 13 d | > 400k tiles | — | 767k | 6,903,039 | 28,794 |
| 31⁷ | 923,521 | 961 | 2.5e16 | 59 d | max_tiles 40k | 8192 | 12,769 | 28,595 | 147 |
| 19⁹ | 2,476,099 | 361 | 4.2e16 | 97 d | 40k + 4 GiB | 16384 | 23,104 | 205,560 | 894 |
| 13¹¹ | 4,826,809 | 169 | 5.1e16 | 119 d | 100k + 4 GiB | 16384 | 87,025 | 781,129 | 3,322 |
| 11¹³ | 19,487,171 | 121 | 5.1e17 | 3.2 y | — | — | 1.4 M | 12,732,107 | 53,834 |
| 127³ | 16,129 | 16,129 | 5.3e14 | 1.2 d | 4 GiB | 512 | 1,024 | 9 | < 1 |
| 139³ | 19,321 | 19,321 | 1.0e15 | 2.3 d | 4 GiB | 512 | 1,444 | 13 | < 1 |
| 157³ | 24,649 | 24,649 | 2.4e15 | 5.4 d | 6 GiB | 512 | 2,401 | 20 | < 1 |
| 179³ | 32,041 | 32,041 | 5.9e15 | 14 d | 14 GiB | 512 | 3,969 | 34 | 1 |
| 199³ | 39,601 | 39,601 | 1.2e16 | 29 d | 14 GiB | 512 | 6,084 | 53 | 1 |

The 11 machines report 2,197 GiB free (74–157 GiB on each of dp-101–108, 857 GiB on dp-156).
The dashboard's check, which also sets space aside for the fields in progress, found 1,134 GiB
available when it refused 11¹³.
At format 2's planning rate, 3²⁵, 7¹³, 5¹⁷, 11¹¹, 17⁹, 19⁹, 31⁷ and the cubes each need under
900 GiB. 3²⁷ and 13¹¹ (about 3.2 TB at the planning rate) fit at the measured rate of
0.002–0.005 B/cell. 7¹⁵, 5¹⁹ and 3²⁹ need [frontier storage](#keeping-only-the-frontier).

## Where these facts come from

The repository's markdown has drifted, so every machine fact above was checked against the live
cluster on 2026-10-03 at 11:45: memory, disk and GPUs from the leader's `nodes` table, and Python
and `lzma` over SSH. Code facts were checked in the code. The transition range a, b, t ∈ 1..p is
in `src/transitions.c`, the recurrence (start at 0, strict improvement, unsigned gains) in
`src/dp_tile.c`, three copies in `cluster/leader.py` and the live `artifacts` table, and memory
admission in `adapter.resource_requirements`. Markdown that turned out wrong or stale:
`docs/CLUSTER_INVENTORY.md` has no dp-156 and older disk figures, `docs/GPU.md` lists only the
P600 hosts, and `docs/DP_SOLVER_CONCERNS.md` says every machine is on Wi-Fi, though 9 of 11 now
have wired addresses (the `nodes` table's `private_group`).

## Changes made that night (since committed and deployed: the live campaign runs `tile_format: 2`)

| file | change |
| --- | --- |
| `dp_solver/tile_codec.py` | New. Format-2 encoder/decoder for packets and bands; damaged xz reports `ValueError` so band fallback still works. |
| `dp_solver/distributed_solver.py`, `cluster/agent.py` | Packs format 2 when the tile spec says `tile_format: 2`; unpacks either format by magic bytes. `gpu_fits()` sends tiles too big for the host's card straight to the CPU; the agent exports its detected cards for it (see below). |
| `dp_solver/bands.py`, `tiles.py` | Bands in either format; `region_rows()` shared with `extract_region()`. |
| `dp_solver/distributed.py` | `tile_format` passed to tiles only when set, so format-1 tile identities are unchanged. Roots above 10,000 tiles are rescanned at most every tiles/10,000 s (≤ 30 s). Shared `durable_tiles()` / `predecessor_coordinates()`. |
| `dp_solver/adapter.py` | Validates `tile_format`. Status tile counts use the bulk durable query instead of one replica query per tile and full descriptors per blocked tile (the same cost `a8725ad` removed from the scheduler). |
| `dp_solver/scheduling.py` | `plan_tiles()` (one copy of the layout search the feeder and dashboard each had) and `stored_bytes()`. A root that fits 2 GiB still reserves 2 GiB; a bigger tile reserves its own need rounded up to 256 MiB, not the whole cap. |
| `campaigns/king_hamming.py` | New feeder settings `max_tiles` (10,000) and `tile_format` (1). Format-2 roots reserve `stored_bytes`; format-1 roots keep the deliberate uncompressed reservation. |
| `web/commands.py`, `web/feeder.py` | Submit and preview use the shared planner, the two settings, and per-format storage rates. |
| `dp_solver/distributed_solver.py` (timings) | Each tile reports `fetch`, `halo`, `gpu_wait`, `kernel`, `pack` and `publish` seconds in its progress details, to explain small tiles' 30 s leases after rollout. |
| `dp_solver/frontier_prototype.py` | The band-only prototype ([below](#prototype-done-tonight)). |
| test isolation | `test_recovery.Cluster` and the bundled-runtime test run agents with `KH_DISABLE_GPU_DP=1` (as `test_distributed` already did). Test tiles were taking the host-wide GPU lock that merlin's live agent uses, waiting up to 120 s for it: that is why three end-to-end tests failed in every full run. The bundled-runtime test also drops `KH_ENABLE_TEST_FIXTURES`, which other modules set at import and which the bundle cannot satisfy. The full suite now passes, 228 tests in 109 s instead of about 320 s. |
| tests | `cluster/tests/test_tile_codec.py` (codec, packets, bands, damage, and a real three-agent format-2 field that matches `kh_dp_local`), planner/feeder tests in `test_scheduling.py`, rate-limit test in `test_tile_reuse.py`, GPU admission test in `test_gpus.py`. |

Defaults are unchanged: with `tile_format` 1 and `max_tiles` 10,000, every root the feeder or
dashboard creates has exactly today's specification.

### GPU admission

`kh_gpu_dp_tile` exits 3 both when the GPU is unavailable and when a tile does not fit in device
memory (`gpu_dp_solver/src/main.c`). `distributed_solver.py` treats any 3 as "GPU unavailable"
and turns the host's GPU off for **every** tile for ten minutes. No current field triggers this.
The largest so far, 113³ at side 512, needs about 1.47 GB, just under a P600's usable 1.70 GiB
(2.09 GB less the 256 MiB `gpus.py` keeps free). But 127³ or 16384-side tiles would trigger it
continuously on the eight P600 hosts. `gpu_fits()` now skips the GPU when a tile needs more than
the host's card can hold. The agent exports the cards it detected (`KH_GPU_DEVICES`), giving
1.70 GiB on dp-101–108, 3.38 GiB on dp-156 (T1000), 3.69 GiB on dp-152 (GTX 1050 Ti) and
5.42 GiB on merlin (RTX 3060). Without that variable it assumes 1.5 GiB, and
`KH_GPU_DP_MAX_BYTES` overrides either. A cleaner long-term fix is a distinct exit code for
"too large" in the GPU kernel.

## Rollout

Format 2 must be readable by every agent before any root uses it, because a format-1 agent
cannot unpack a format-2 predecessor. Roots record their format, so the order is:

1. Restart the leader (it runs from the working tree) and upgrade the agents' runtime. Nothing
   changes yet: all roots are format 1.
2. Set the feeder's `tile_format` to 2 (dashboard → Feeder limits). New roots use it; running
   roots keep format 1 to completion, and their retained tiles are still reused by retries.
3. To admit large-budget fields, raise `max_tiles` (40,000 covers 3²⁵, 7¹³, 17⁹; 100,000 adds 3²⁷
   and 13¹¹) and, if wanted, `max_tile_bytes` to 4 GiB. The 4 GiB setting reserves at most what
   each field needs. Watch the scheduler's lock time (the leader log's lock errors, or the
   dashboard's Problems tab) after the first large root starts.
4. For a large-prime cube, raise `max_tile_bytes` for that submission (127³ reserves 2.25 GiB;
   199³ reserves 12.25 GiB, so only merlin or an otherwise idle 15.5 GiB worker can run its tiles;
   dp-156 never can).

The feeder's `frontier_max_*` settings still decide which fields it proposes on its own; the
dashboard's *Submit field* is the way to queue a specific one.

## Remaining per-tile costs at 100k+ tiles

Not changed tonight, and worth fixing before a 100k-tile root:

- `retire_finished_tiles`, `reuse_tiles` and the rows query in `advance()` still touch every tile
  of a root on each (rate-limited) scan. An incremental frontier would need only the unassigned
  tiles next to assigned ones. Tracking the lowest open wave (row + column) per root bounds it.
- The dashboard's tile grid sends one cell per tile (85k cells for 3²⁷).
- Per-tile overhead dominates small primes. 5¹³ tiles (side 1024) hold their leases about 35 s
  each for well under 1 s of computation. The rest is lease handling, input transfer, packing and
  publication; I did not break it down. Format 2 shrinks the transfers about 100×, so measure
  this again after rollout before choosing sides for 3²⁵-class fields.
- Choices could be 16-bit for p ≤ 40 and values 32-bit, which would reduce tile memory by
  half. That is a change to both kernels.

## Keeping only the frontier

Forward computation needs, for each new tile, only the last p² rows and columns of the tiles
just before it: the bands. If tiles are scheduled in antidiagonal waves, the live set is about two
waves of bands, `2·B·p²` cells. For 3²⁹ that is 258 M cells (about 2 GB of values), so a
wavefront DP fits easily.

Reconstruction is the obstacle. It walks back from (B, B) following each cell's stored
choice, and which cells it visits is unknown until the walk is done. Ways to get the
choices without keeping every tile, in increasing order of savings:

1. **Keep every tile's bands, drop its packet; recompute path tiles at reconstruction.** Bands
   are all a tile's successors read, so any tile can be recomputed exactly from its three
   predecessors' bands. Storage falls by about `S / (2p²)`. For 3²⁹ at side 16384 that is about 900×,
   so 28.8 TB becomes about 30 GB. The walk crosses at most `2·B/S` tiles, so reconstruction recomputes
   about `2/count` of the field, for example 0.2% for 3²⁹. The catch: it is sequential (each tile's entry
   point comes from the next one), which is hours for large tiles.
2. **Add exit pointers to make that parallel.** While packing a tile, also record for each band
   cell `J(x)`, the first cell outside the tile on x's backward path. It is computed from
   `choices.bin` in one pass, because every transition moves at least 1 in both coordinates. A
   path leaves a tile into its halo, which is a predecessor's band. So the chain
   `(B,B) → J → J → …` lists every tile the optimal path crosses, and its entry cell, from band
   data alone. All path tiles can then be recomputed at once, each only up to its entry cell, and
   their run-lengths concatenated. This costs one 64-bit pointer per band cell, which compresses like the
   values.
3. **Checkpoint lines for `O(B·p²)` total storage.** Keep values only on every K-th tile row
   and column boundary; reconstruct by recomputing the K×K blocks the path crosses, using
   method 2 inside each block. This is for 11¹³-sized fields, which are out of reach on compute
   time anyway.

### Prototype (done tonight)

[`dp_solver/frontier_prototype.py`](../dp_solver/frontier_prototype.py) runs methods 1 and 2
end to end on one machine with the production `kh_dp_tile`. Each tile is computed from its
predecessors' bands only. The tile keeps its bands and their exit pointers, and its values and
choices are deleted at once. Reconstruction follows the J chain, then recomputes and traces each
crossed tile. Its output equals `kh_dp_local`'s split exactly (theta and every run) on ten
configurations: 2⁹, 3⁷, 3⁹, 5⁵, 5⁷, 7⁵ and 11³, with sides from 5 to 100, including tiles
narrower than the halo (3⁷ at side 5, 11³ at side 23). Every chain matched the traced segments.
`cluster/tests/test_frontier_prototype.py` keeps four of those cases.

J and tracing are pure Python there, fine for these sizes but not for 16384² tiles.

### Production plan, if wanted

1. **J in C.** A small pass over `choices.bin` after either kernel (CPU or GPU), writing J for the
   band cells only. Store it inside format-2 bands; it compresses like the values.
2. **Packet-free retention.** Once a tile's bands (with J) have their replicas, delete its packet.
   The leader then offers bands, not packets, for reconstruction inputs.
3. **Parallel reconstruction.** Read the chain from the bands, lease one recompute-and-trace task
   per crossed tile (about count to 2·count of them), and concatenate.
4. **Frontier-only tile rows in the leader,** so a 767k-tile root does not keep 767k rows of
   scheduling state. Tiles enter the table when their predecessors are durable.

## Large-prime cubes

For r = 3, B = p², so every tile's halo is everything below and to its left. A side-512 tile
of 199³ reads up to an 11.7 GiB halo to compute 0.26 M cells. Raising the reservation (above) makes
them run, one tile per machine. To do better, the kernel would stream the halo in strips,
applying the transitions whose sources lie in each strip and keeping the earliest best
transition, which preserves the first-strict-improvement tie rule. Memory would then be the tile plus one strip.
That is kernel work for both CPU and GPU, and it only matters if these fields become a priority.
