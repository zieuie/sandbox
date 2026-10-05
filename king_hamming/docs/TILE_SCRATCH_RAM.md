# DP tile scratch in RAM (2026-10-04)

Every DP tile used to write about 350 MB of temporary files to its machine's SSD and delete
them seconds later. Across the cluster that was several terabytes a day, enough to wear out
merlin's drive in about three months. The tile solver now keeps those files in RAM
(`/dev/shm`). Disk writes fell by 94% on merlin and to almost nothing on the workers, and
merlin's tile rate rose by a third.

## The problem

A 4096-side tile writes three large scratch files:

| File | Size (29⁷ tile) | Written by |
|---|---|---|
| `halo.bin` (the tile plus its predecessor margin) | ~195 MB | the solver, before waiting for the GPU |
| `tile-output/values.bin` | 134 MB | the CPU or GPU kernel |
| `tile-output/choices.bin` | 67 MB | the CPU or GPU kernel |

Only the tile's packet (about 57 KB) and its edge bands (about 25 KB) are kept; they're
published to the blob store. The scratch files lived in the lease's work directory on the
SSD and were deleted once the tile was acknowledged.

Measured before the change:

| Machine | Tiles/h | Disk writes |
|---|---|---|
| merlin | ~630 | 199 GB/h (4.6 TB/day) |
| each P600 mini-PC | ~68 | ~20 GB/h (0.5 TB/day) |
| gawain | ~150 | ~0.9 TB/day (from boot-time counters) |

The write rates come from `/proc/diskstats` over 5–10 minutes. merlin's drive reported 8% of
its rated endurance used after 39 TB written, so at 4.6 TB a day it was gaining about 1% a
day. merlin is the leader and the only big-memory matcher, so losing its drive would stop
the campaign.

## Why RAM works

- **The files never need to survive the process.** A failed or interrupted tile is simply
  recomputed under a new lease, so nothing durable is lost.
- **They're small next to the memory already reserved.** About 400 MB per tile, against the
  2 GiB the leader reserves for each tile (whose process only uses about 0.33 GiB).
- **The concurrency is low.** For the current fields there are 2–7 tiles in flight per
  machine, so 1–3 GB of RAM at most.
- **No system changes are needed.** `/dev/shm` is a tmpfs on every Ubuntu host, sized at half
  of RAM by default.

## What changed

Commit `e499dee`:

- **`dp_solver/tile_scratch.py` (new).** A tile *claims* a private directory in `/dev/shm`
  sized for its halo plus 1.5× its value bytes plus 16 MiB of slack.
  - **Bounded.** Claims are counted under a file lock. A claim fits only if all live claims
    together stay under a quarter of RAM (`KH_TILE_SCRATCH_MAX_BYTES`) and the tmpfs keeps
    256 MiB free.
  - **Self-cleaning.** Each claim records its process ID and start time. A claim whose
    process has died (killed before its cleanup ran) is deleted by the next claim, and a
    reused PID can't keep a stale claim alive.
  - **Fallback.** A tile that doesn't fit uses the work disk exactly as before.
    `KH_TILE_SCRATCH=disk` forces that; `KH_TILE_SCRATCH=<dir>` picks another tmpfs.
- **`dp_solver/distributed_solver.py`.** `compute()` claims scratch, puts `halo.bin` and the
  kernel's `tile-output/` there, and always releases the claim. Inputs and the packet stay on
  disk: inputs are small bands except in the rare whole-packet fallback (about 1% of tiles),
  and the agent publishes the packet after the solver exits. Each tile now reports
  `"scratch": "ram"` or `"disk"` in its progress details.
- **Kernels unchanged.** `kh_dp_tile` and `kh_gpu_dp_tile` stage their output next to the
  destination and rename it into place, which works the same on a tmpfs.

**Tests:**
- `cluster/tests/test_tile_scratch.py`: limits, release, sweeping a dead process's claim,
  a reused PID, the disk fallback and a full tmpfs.
- `gpu_match_solver/tests/check_cluster.py` now requires every tile of its end-to-end
  distributed 5³ run to use RAM scratch and leave nothing behind. The result is still checked
  byte for byte against the single-machine DP.
- The cluster suite passes: 286 tests.

## Rollout

| Step | When | How |
|---|---|---|
| merlin | 2026-10-04, evening | `upgrade-worker-rolling --host 192.168.4.151`, then measured |
| 8 mini-PCs and gawain | 19:00–19:31 | `upgrade-worker-rolling`, one host at a time, under a minute to 7 min each |

Each upgrade stopped new work to one machine, waited for its tiles to finish and restarted
its agent; the rest of the cluster kept working, and no upgrade failed. Afterwards all 10
machines run the same runtime version, and every tile in the next 5 minutes on every machine
used RAM scratch.

## Results

| Machine | Disk writes before | After | Tiles/h before | After | GPU busy before | After |
|---|---|---|---|---|---|---|
| merlin | 199 GB/h | **12.8 GB/h** | 630 | **840** | 65% | **92%** |
| fearless, showgirl, gawain | ~20 GB/h (gawain ~40) | **under 1 GB/h** | — | — | — | — |

- **merlin got faster too:** +33% tiles per hour. Its 3060 finishes a tile in about
  6 seconds, so per-tile overhead used to leave the GPU idle a third of the time. The other
  GPUs were already 93–98% busy, so expect little speed change there; their gain is drive
  life.
- **What merlin still writes** (about 0.3 TB a day) presumably comes from the leader's
  database, its share of stored tile copies and matching output. That breakdown hasn't been
  measured.

**Caveats:**
- merlin's comparison is a 10-minute window before against 9 minutes after; a longer
  comparison would firm up the speedup.
- The worker write rates were measured over 5 minutes, rounded down to whole GB/h.

## Drive health (2026-10-04, after the rollout)

Read from each drive's NVMe SMART log (log page 2, read-only). All drives report no critical
warnings, no media errors, 100% available spare, and 35–48 °C.

| Machine | Drive | Wear used | Written | Power-on | Unsafe shutdowns | Years left at old rate | Years left now |
|---|---|---|---|---|---|---|---|
| fearless | Samsung PM961 256 GB | **38%** | 82.8 TB | 42,186 h | 558 | ~0.7 | >15 |
| red | Samsung PM981 256 GB | 10% | 29.5 TB | 8,025 h | 78 | ~1.4 | >15 |
| lover | Samsung PM981 256 GB | 6% | 23.3 TB | 13,810 h | 69 | ~2 | >15 |
| folklore | Samsung PM961 256 GB | **29%** | 119.5 TB | 7,841 h | 129 | ~1.6 | >15 |
| evermore | Micron 2200 256 GB | 4% | 12.6 TB | 14,148 h | 618 | ~1.6 | >15 |
| midnights | Samsung PM981 256 GB | 6% | 23.9 TB | 10,741 h | 97 | ~2 | >15 |
| poets | Samsung PM961 256 GB | 8% | 30.2 TB | 6,773 h | 135 | ~1.9 | >15 |
| showgirl | Samsung PM981 256 GB | 9% | 33.2 TB | 9,417 h | 120 | ~1.8 | >15 |
| merlin | Samsung PM981 512 GB | 8% | 39.2 TB | 9,576 h | 446 | **~0.25** | ~4 |
| gawain | Kingston OEM 1 TB | 15% | 27.8 TB | 11,283 h | 22 | **~0.5** | >15 |

**How to read it:**
- *Wear used* is the drive's own estimate (SMART "Percentage Used") against its rated
  endurance. Drives often keep working past 100%, so the year columns are rough horizons,
  not failure dates.
- The projections take each drive's wear per terabyte so far and apply the write rate before
  the change (about 0.5 TB/day on mini-PCs, 4.6 on merlin, 0.9 on gawain) and after it
  (under 24 GB/day on mini-PCs and gawain, about 0.3 TB/day on merlin).

**What stands out:**
- **merlin was the most urgent:** about 3 months left at the old rate, about 4 years now.
  It still writes the most of any machine, and it matters most, so it's the drive to watch.
- **gawain's Kingston wears about twice as fast per terabyte** as the Samsungs (15% after
  27.8 TB), which suggests lower-endurance flash. At the old rate it had about 6 months left.
- **fearless has the most-worn drive (38%) and the most power-on time** (about 4.8 years). With
  writes this low, its age is now the bigger risk, not wear.
- **Many unsafe shutdowns** (power cut or hard reset without a clean shutdown) on evermore,
  fearless and merlin. That isn't a wear problem, but a cheap UPS for merlin would protect the
  leader's database.
- pellinore (retired and powered off the same day) had a Samsung PM981 256 GB at 7% wear
  after 83.5 TB.

## Follow-ups

- **Speed:** compare tile rates over a few hours to confirm the speedup outside merlin.
- **merlin:** re-read its SMART log in a few weeks to confirm the new wear rate. If it stays
  high, find what writes the remaining 0.3 TB a day.
- **Ongoing monitoring:** the dashboard could show each drive's wear and unsafe-shutdown
  count; today they're only visible by reading SMART by hand (`nvme-cli` and `smartctl` aren't
  installed on the hosts).
