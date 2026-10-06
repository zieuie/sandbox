# Plan: placing DP tiles on GPUs by completion time, dependencies and field progress

Status (2026-10-06): **steps 1, 3 and 5 built** (simulator, critical-path order, earliest-completion
placement with tail mode), **off by default**. Leader setting `dp_tile_placement`: `pull` (today's
order) or `ect`. Step 4 (an in-flight cap) was dropped: the simulator showed it doesn't help (see
"Simulation results" below). Proposed 2026-10-05, with measurements from the live campaign (29⁷ and
31⁷ DP, ten machines) on the night of 2026-10-04.

## Simulation results (2026-10-06)

`dp_solver/placement_sim.py` replays a finished field: its tile DAG, every machine's slots and its
one GPU (kernels in order of arrival), and each tile's fetch, kernel, pack and publish times as
recorded. Kernel times move between GPUs by their measured ratio: P600 ×12, T1000 ×5.2, against
merlin's 3060.

**Replay of 31⁷ alone**, from 17:40 on 10-05 (after 29⁷'s DP) to the end. merlin's GPU is held
until 20:12 by 29⁷'s matching, as it really was. The real run took **18.1 h**; the simulator gives
19.2 h for today's policy (7% pessimistic), so it's close enough to compare policies.

| Policy | Time | Last ~40 diagonals | merlin's GPU busy | P600s busy |
|---|---|---|---|---|
| today (pull, cheapest first) | 19.24 h | 97 min | 78% | 98% |
| critical-path order only | 18.57 h | 95 min | 84% | 98% |
| plus in-flight cap of 2 | 17.41 h | 60 min | 96% | 99% |
| in-flight cap of 1 | 21.13 h | 53 min | 69% | 89% |
| **`ect`, as built (no cap)** | **17.08 h** | **39 min** | **99.7%** | 98.5% |

- **The gain is merlin's.** Under pull, the P600s hold tiles that merlin finishes 12 times faster,
  so merlin's GPU waits. With `ect`, critical tiles wait a few seconds for merlin instead.
- **The in-flight cap** is a blunt version of the same idea: at 2 it helps, at 1 it starves every
  GPU, at 3–4 it does nothing. `ect` gets more without it, so it was dropped.
- **For a field like 17⁹** (about 1.4 days of DP), 11% is roughly 4 hours, about an hour of it in
  the tail.
- **Run-to-run variation:** under 1% across 3 random orders of machines asking.

**How `ect` decides** (`cluster/placement.py`):
- **Order:** a field's tiles go lowest anti-diagonal first (ties by age). Reconstruction first,
  priority and fairness between fields are the leader's as before.
- **Critical tiles:** a tile on its field's 3 lowest unfinished anti-diagonals, or any tile once
  820 or fewer remain (about the last 40 diagonals).
- **The rule:** a critical tile is skipped for the asking machine when another live, unpaused GPU
  machine would finish it sooner by more than one kernel on the fastest GPU. The estimate for a
  machine is: wait for a slot if it's full, then its queue of tiles before their kernels, then its
  overhead, the kernel and the overhead after.
- **The model:** medians of each machine's last 200 completed tiles on that field (at least 5),
  refreshed every 30 s. Running-tile counts are refreshed every 2 s. A machine with no history on
  the field never has a tile held back, and machines whose GPU is held by a GPU lease (a matching)
  are not counted as alternatives.
- **Safety:** nothing is pushed, so a dead machine simply stops asking. Leases, fencing and
  recovery are unchanged, and setting it back to `pull` takes effect at the next lease.

**To turn it on:** `INSERT OR REPLACE INTO settings(key,value) VALUES('dp_tile_placement','ect')` in
the leader database (or through a future dashboard toggle). No restart is needed.

## Why

The DP is GPU-bound: in the last 6 hours, 9,298 of 9,320 tiles of 29⁷ and 31⁷ ran on a
GPU ([MACHINE_CONTRIBUTIONS.md](MACHINE_CONTRIBUTIONS.md)). The GPUs differ by an order of
magnitude, and the scheduler ignores that.

| GPU | Machines | Kernel per tile (29⁷) | Wall per tile, including its GPU queue |
|---|---|---|---|
| RTX 3060 Laptop | merlin | 5.5 s | ~27 s |
| Quadro T1000 | gawain | 18.5 s | ~36 s |
| Quadro P600 | 8 mini-PCs | 47–57 s | ~160 s |

**How tiles are handed out today.** The queue is pull-based. Whichever machine's slot asks
next gets the next queued tile, ordered by:
1. reconstruction work first;
2. priority;
3. fairness across fields (the field with the fewest running tiles first);
4. estimated runtime, then age;
5. a small tie-break for machines that already hold the tile's left neighbour
   (`locality_order`).

It never asks how fast the asking machine's GPU is, how many tiles are already queued for
it, or whether a tile is on the critical path.

**What that costs, observed:**

1. **A ragged wavefront.** A tile depends on its upper, left and upper-left neighbours,
   so the DP advances along anti-diagonals. On 29⁷ the front spans 49 diagonals at once
   (161–209). The lowest, which everything behind depends on, are often *ready but
   queued* for 6–10 minutes, while newer tiles at the leading edge are picked first
   (smaller edge tiles have shorter estimates and jump ahead).
2. **Hoarding by slow GPUs.** Each mini-PC leases about four tiles at a time for one P600.
   The fourth waits about 100 s for the GPU, then takes about 57 s. merlin would have
   finished it in a few seconds.
3. **CPU fallbacks on the critical path.** A tile that waits half its estimated CPU time
   for the GPU (11–14 min here) falls back to the CPU and takes 25–50 min. In the snapshot
   above, one of the lowest 31⁷ tiles had been on a CPU for 22 minutes. (Addressed
   separately: CPU fallback becomes an operator toggle, off by default.)
4. **Slow tails.** Each field ends with about 39 anti-diagonals narrower than 40 tiles,
   which is fewer than the cluster's running slots. There, finish time is set by the
   critical path, not throughput. A diagonal finishes when its slowest tile does, and
   under pull scheduling most tail tiles land on P600s. 7¹³ (light tiles) took 22 min for
   its last 39 diagonals, 34 s per diagonal. For 29⁷ and 31⁷, at P600 speed plus queueing,
   that's an estimated 60–100 min per field; on the fastest GPUs it would be about 15–20.

**Where the gains are:**
- **Mid-field:** ready work is plentiful and the GPUs are 87–100% busy, so placement can
  add only a few percent of throughput there, mostly merlin's.
- **Tails:** this is the real prize. It's likely 45–80 minutes saved per field, and the
  next field's work can start sooner.

## Design

### 1. Estimate a tile's completion time on each machine

For each live node, the leader keeps (in memory, rebuilt from `progress_details` on start):

- **Service time `s(node, field)`:** an exponentially weighted mean of `kernel_seconds` plus
  the measured per-tile overhead (fetch, halo, pack, publish), per field, since transition
  counts differ by field. Before a node has data for a field, use the GPU's ratio to
  merlin on the previous field.
- **Queue ahead `q(node)`:** tiles leased to the node and not yet past their kernel. The
  `"waiting for GPU"` heartbeat added on 2026-10-05 makes this observable.

The estimated completion time of a tile leased to `node` now is

    ECT(node) = now + q(node) · s(node, field) + s(node, field)

This is cheap: about ten nodes, one multiplication each.

### 2. Rank ready tiles by criticality, not age

For a tile at (row, column) in a grid of R × C tiles:

- **Remaining path** `L = (R − 1 − row) + (C − 1 − column)`: the number of anti-diagonals
  that must still follow it. The lowest diagonal has the largest L.
- **Blocked successors** `B`: how many of its three successors are waiting only on it.

Ordering *within a field* becomes: lowest anti-diagonal first, ties by larger B, then age.
That's the classic critical-path-first rule for wavefronts: it keeps the front tight, so
more tiles become ready sooner. Fairness *across fields* stays (fewest running tiles first),
and priority and reconstruction keep their precedence.

### 3. Place by earliest completion, with a "leave it for a faster GPU" rule

When node X asks for work, walk the ranked tiles (the existing candidate list of up to 100):

- **Critical tiles** (on one of the k lowest unfinished diagonals of their field; k = 3 to
  start). Lease it to X only if ECT(X) ≤ min over live nodes of ECT(node) + slack, with
  slack about one service time on the fastest GPU. Otherwise skip it: a faster GPU will
  ask within seconds. X gets a non-critical tile instead.
- **Other tiles** go first-come as today. Mid-field, slow GPUs stay fully used on
  non-critical work.
- **Never idle a node** only to protect a tile. If every remaining ready tile is critical
  and X loses all of them, X waits for the next ask (a few seconds). That's the intended
  behaviour in a tail, not a deadlock: the faster nodes keep pulling.

This is a pull-based version of earliest-finish-time list scheduling (HEFT). Nothing is
pushed, so a node that dies just stops asking, and leases, fencing and recovery are
unchanged.

### 4. Bound per-node in-flight tiles to what keeps its GPU busy

A GPU needs about two tiles in flight: one on the GPU and one with its inputs ready. A cap
of `max_inflight(node) = 2` (setting `dp_tile_inflight`, per node or global) stops the
P600s from hoarding four tiles. This might be the single largest mid-field change, because
the freed tiles go to merlin and gawain. Measure before tightening: too low a cap would idle
a GPU during the input fetch.

### 5. Tail mode

When a field's next unfinished diagonals are narrower than the number of live GPUs × 2:

- the "leave it for a faster GPU" rule applies to *all* of that field's tiles, not just
  the k lowest diagonals;
- slow GPUs take a tail tile only if their ECT beats the fastest GPU's by queueing, which
  happens only when a fast GPU's queue is genuinely full;
- the next field's tiles, if any, fill the slow GPUs, so nothing idles.

### 6. Interactions

- **CPU fallback toggle (off by default):** tiles never fall back to a CPU, so ECT doesn't
  need a CPU branch. If an operator turns CPU fallback on, it remains a leaf decision on
  the agent, unchanged.
- **Long GPU holds** (block matching on merlin): ECT counts a held GPU as unavailable
  (q = ∞), so tail tiles go to gawain and the P600s instead of waiting behind a matching.
- **Locality tie-break:** keep it, but below criticality. A local predecessor saves about
  1 s of fetch, against tens of seconds of placement difference.

## Implementation plan

1. **Simulator first (no live risk).** Write a small discrete-event simulator that replays
   a field's DAG with service times drawn from the recorded `progress_details` per GPU
   class. Compare four policies on 29⁷ and 31⁷ as they stand today and on 7¹³'s full
   history:
   - today's ordering;
   - critical-path ordering alone (§2);
   - plus the in-flight cap (§4);
   - plus ECT placement and tail mode (§3, §5).

   Report the total time and the tail time (front narrower than 40 → field complete).
   Ship only what the simulator shows helps.
2. **Telemetry.** Add the front's span (diagonals in flight), the age of the oldest ready
   lowest-diagonal tile, per-node queue depth and tail duration to the dashboard's DP
   tiles page and to `/v1/status`. These are the before/after numbers.
3. **Critical-path ordering (§2).** A change to the candidate `ORDER BY` plus `B` from the
   tile table. Leader-only (a live restart), easily reverted.
4. **In-flight cap (§4).** A leader setting, checked in the lease path. Leader-only.
5. **ECT placement and tail mode (§1, §3, §5).** The service-time model in the leader's
   memory, consulted in the lease loop. Leader-only. Behind a setting
   (`dp_tile_placement = "pull" | "ect"`) so it can be switched off live.
6. **Measure for a day.** Compare the front span, queue ages and the next tail against the
   simulator's prediction and against 7¹³'s tail. Write the outcome into this document.

All steps are leader-only (no worker rollout) and each is behind a setting or a single
revertable commit.

## Risks and how they're handled

- **Leader cost in the lease path.** ECT is O(nodes) per candidate on at most 100
  candidates, so microseconds. The service-time model updates on `/v1/complete`, which
  already writes. No new database scans: B is read only for the candidates.
- **Starving slow GPUs.** Only critical tiles are withheld, and only by at most the slack.
  Mid-field, slow GPUs keep the full stream of non-critical tiles.
- **Model errors** (a GPU throttles, or a field's kernel time changes). EWMA with a short
  half-life, and the slack absorbs small errors. The setting switches back to pull.
- **Fairness between fields.** Unchanged ordering across fields. Tail mode for one field
  fills slow GPUs with the other field's tiles, which also lets the next field start
  sooner.
- **Hidden coupling with reconstruction and matching** (whole-host leases). Unchanged: they
  keep their precedence and their own placement rules.
