# How much each machine contributes (2026-10-04)

The machines aren't equal, and for DP the difference is almost entirely the GPU. merlin's
laptop RTX 3060 does about half of all the work, and gawain's T1000 another tenth. Each of
the eight P600 mini-PCs does about 5%, and together they make up the remaining two-fifths.
pellinore did no more than a mini-PC at the highest energy cost per tile, so it was retired
the same day.

## The numbers

### Before the changes (6 hours to about 18:00, 8,040 tiles of 29⁷ and 31⁷)

| Machine | GPU | Tiles/h | Share of DP | Power (CPU + GPU) | Energy per tile | Failed or expired leases (3 days) |
|---|---|---|---|---|---|---|
| merlin | RTX 3060 Laptop | 568 | **42.4%** | ~34 + 107 W | ~0.28 Wh | 0.7% |
| gawain | Quadro T1000 | 156 | **11.6%** | ~17 + 30 W | ~0.35 Wh | 1.0% |
| each of 8 mini-PCs | Quadro P600 | 67–70 | **5.0–5.2% each, 41% together** | ~5–12 W + up to 40 W\* | ~0.8 Wh | 2.0–2.3% |
| pellinore | GTX 1050 Ti Max-Q | 70 | **5.2%** | ~24 W + up to 40 W\* | ~1.0 Wh | **3.5%** |

\*These GPUs can't report their power draw, so their rated maximum is used. The board, disk
and fans add an unmeasured ~5–15 W per machine.

### After retiring pellinore and moving tile scratch to RAM (19:32–19:53, 548 tiles)

| Machine | Tiles/h | Share of DP |
|---|---|---|
| merlin | 825 | **52.7%** |
| gawain | 168 | **10.8%** |
| each of 8 mini-PCs | 66–77 | **4.2–4.9% each, 36% together** |
| **Whole cluster** | **~1,560** (was ~1,340 with pellinore) | |

This window is only 21 minutes, so treat it as a first look. merlin's jump comes from the
RAM scratch change ([TILE_SCRATCH_RAM.md](TILE_SCRATCH_RAM.md)), which removed per-tile
disk overhead that had left its GPU idle a third of the time. It more than made up for
losing pellinore.

## Why the machines differ so much

For the fields being computed (29⁷ and 31⁷), a tile takes 1,600–2,400 s on 2 CPU threads
and 5–60 s on a GPU, so DP runs almost entirely on GPUs: only 15 of about 7,900 tiles in
the 6-hour window ran on a CPU. Each machine runs one GPU tile at a time, so its
throughput is set by its GPU's kernel time:

| GPU | Kernel time per 29⁷ tile | 31⁷ |
|---|---|---|
| i7-7700T, 2 threads (CPU, for comparison) | ~1,580 s | ~2,440 s |
| Quadro P600 | 47 s | 58 s |
| GTX 1050 Ti Max-Q | 48 s | 61 s |
| Quadro T1000 | 18.5 s | 25 s |
| RTX 3060 Laptop | 5.5 s | 6.5 s |

The GPUs were 93–98% busy on every machine except merlin (64% before the RAM scratch change,
92% after). The CPU cores barely matter for DP: a whole 4-core i7-7700T is worth about an
eighth of its P600.

The 1050 Ti Max-Q has twice the P600's cores and more memory bandwidth, yet runs at the same
speed. A power- or heat-limited laptop part is the likely reason. That's why pellinore
contributed no more than a mini-PC.

## How these were measured

Everything comes from data the cluster already records, plus two short live readings
(power, and the SMART data in the companion report). Nothing was benchmarked separately.

### 1. Work done: the leader's run records

Every tile is a row in the leader's `runs` table (`cluster/deployments/continuous-campaign/
leader.sqlite`): which machine ran it (`node_name`), when it started and finished, how many
DP cells it covered (`progress_total`), and a JSON `progress_details` record the tile solver
writes at the end. That record names the engine (`"gpu"` or `"cpu"`) and how long each
phase took: fetching inputs, building the halo, waiting for the GPU, the kernel, packing and
publishing.

The share column is each machine's DP cells divided by the total over the window, which
weights edge tiles (smaller than 4096²) correctly. Tiles per hour is just the count divided
by the window. In outline:

```sql
SELECT node_name, COUNT(*) AS tiles, SUM(progress_total) AS cells
FROM runs
WHERE json_extract(specification, '$.program') = 'dp_tile'
  AND state = 'complete' AND finished > :window_start
  AND json_extract(specification, '$.arguments.p') IN (29, 31)
GROUP BY node_name;
```

Restricting to one pair of fields matters: a 7¹³ tile has far fewer transitions per cell
and finishes several times faster on a GPU (and hundreds of times faster on a CPU), so mixing
fields would compare different amounts of work.

The kernel-time table is the median of `progress_details.kernel_seconds`, grouped by field,
machine type and engine.

### 2. How busy each GPU is: the agents' samples

Each agent samples `nvidia-smi` utilisation with every heartbeat, and the leader keeps the
samples for 7 days (`gpu_usage_samples`). The busy percentages are averages over the last
3 hours. They showed the GPUs were the bottleneck, so per-GPU speed decides each machine's
contribution.

### 3. Power: a 20-second live reading on each machine

- **CPU:** the Intel RAPL energy counter (`/sys/class/powercap/intel-rapl:0/energy_uj`,
  read with sudo) at the start and end of 20 seconds, giving the CPU package's average watts.
- **GPU:** `nvidia-smi --query-gpu=power.draw` once a second for 20 seconds, averaged. The
  P600 and 1050 Ti return "N/A", so their rated maximum (40 W) stands in.
- **Energy per tile:** the estimated total (CPU + GPU + ~10 W for the rest of the machine)
  divided by tiles per hour.

This is the weakest part of the analysis: it's one short sample, it misses the board, disk,
fans and power-supply losses, and two of the GPU figures are ratings, not measurements. A
plug-in power meter would give real wall numbers. The ranking is robust anyway: merlin and
gawain are several times more efficient per tile than the P600 machines, whatever the exact
watts.

### 4. Reliability: lease history

Every lease ends with a recorded outcome in `lease_history` (complete, fail, lease expired,
engine retry, intentional stop). The failure column is the share of a machine's leases over
3 days that failed or expired. These are spread evenly across the mini-PCs (2.0–2.3%) and
mostly come from cluster-wide incidents (a leader stall during the 13⁹ matching, upgrades),
not from bad machines. pellinore's 3.5% was the outlier, partly from its restarts that day.

## What it led to

- **pellinore retired** on 2026-10-04: drained, removed from the manifest, marked in the
  leader's `retired_nodes` setting so the dashboard doesn't report it as down, and powered
  off. Each of its 21,102 stored copies also existed on at least two other live machines.
  Its two 8 GB DDR4 SO-DIMMs are spare.
- **Hardware direction:** GPU speed is the lever for DP. One full-power modern GPU
  outperforms all eight P600 mini-PCs; see [HARDWARE_BRIEF.md](HARDWARE_BRIEF.md).
- **The mini-PCs still earn their keep** for now: about 36% of DP together, at an estimated
  50–60 W each. They also hold a share of the tile copies, which are kept 3 times across
  machines. Replacing them makes sense once a faster GPU machine can absorb their share.

## Reproducing

To repeat the measurement:
1. Run the query above over a window of a few hours.
2. Average `gpu_usage_samples.util_percent` per node over the same window.
3. For power, read RAPL and `nvidia-smi` on each host as described, or better, use a plug-in
   meter.
