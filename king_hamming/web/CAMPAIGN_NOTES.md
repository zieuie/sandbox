# Campaign library: changes that would give better control

These notes come from building the dashboard. Each item describes a
limitation in the campaign code (leader, feeder, launcher, solvers), what it
causes in practice, and a possible change. The dashboard works around some of
them; the workarounds are listed so they can be removed once the library
changes. Item numbers are stable, because other documents and the dashboard
refer to them.

**Open items come first, then completed ones.** Status as of 2026-10-02
(evening).

## Deployment status: read this first

Changes made on 2026-10-02 that are **in the working tree but not yet running**
on the campaign (the campaign is stopped, and an upgrade restarts the leader,
agents and feeder):

| Change | Items | Needs |
| --- | --- | --- |
| Finished fields' tiles are deleted; surplus copies are trimmed; replication waits out short outages; scratch is removed | 22, 23, 24 | leader and agents |
| Edge bands: a tile fetches about 1.5 MiB of inputs instead of about 65 MiB | 20 | leader and agents |
| Soft row affinity in lease choice | 21 | leader |
| Free-disk floor on new leases and copies; disk checks in the Submit preview | 7, 8 | leader (and the dashboard) |

To deploy: `launch_dp.py upgrade-leader`, then `upgrade-workers`, then restart the
dashboard (`web/restart_dashboard.sh`). Then compare the first tiles' `input_mode`
and `input_bytes` (see item 20) with the old ones.

Already done by hand on 2026-10-02, while everything was stopped: the equivalent
of items 22–24 was applied to the live data (see the completed section), which
freed 1.23 TB, and the retired deployments' storage was removed from every
machine.

---

# Open items

Suggested order of work: **1** (feeder backlog), **2** (a failed root stops its
tiles), **3** (process supervision), **4** (cancelled tiles), **5** (the rest of
tile timeouts), **7** and **8** (finish the disk checks), and, before the leader
faces anything beyond the home network, **18** (leader authentication).

## Highest value

1. **The feeder should own an ordered backlog of fields.**
   - **Today:**
     - The feeder adds new roots only from `scheduling.regional_campaign()`, in
       raw-visit order, within `target_dp_roots` / `max_dp_roots`.
     - Fields an operator chooses (the dashboard's Submit, or
       `launch_dp.py extend`) bypass it and start at once at priority 0.
     - The leader then orders equal priorities by estimated runtime, so
       whichever field is smallest runs first. That is what starved 101³'s
       reconstruction behind 7¹¹ (item 19, since fixed in the leader).
     - There is no way to say "these fields next, in this order, a couple at a
       time".
   - **Wanted:** an operator-ordered list of fields that the feeder releases in
     order, a few at a time, plus an explicit order for the operator's fields
     that are already active.
   - **Proposed design:**
     - **Data:** `pipeline.json` gains an ordered list, for example
       `"backlog": [{"p": 11, "r": 9, "added": …, "by": "zooey"}, …]`, and
       settings such as `backlog_active` (how many backlog fields run at once;
       2 is a sensible default) and `backlog_priority_top` /
       `backlog_priority_step` (for example 90 and 10). The feeder already
       round-trips unknown keys, and edits happen under `pipeline.lock`.
     - **Release:** each reconcile pass, before expanding its own frontier,
       the feeder counts active roots it released from the backlog (marked
       `"origin": "backlog"` in their manifest entries). While that count is
       below `backlog_active`, and the disk checks allow (items 7 and 8), it
       pops the first entry, builds the specification, enqueues it and records
       it in the manifest, all within the lock it already holds.
     - **Tile side:** choose it per field, as the dashboard's `tile_plan()`
       does (11⁹ needs 2048, not 512). That function belongs in `dp_solver`,
       so the feeder and dashboard share one copy (see item 12).
     - **Order becomes priority:** active backlog roots, then waiting entries,
       take priorities by position, top first. Matching stays above them at
       `matching_priority` (100), and the feeder's own frontier stays at 0.
       When the order changes, the feeder updates each affected root and its
       already-queued tiles. A leader run command that reprioritizes a root
       together with its queued children would make that one call per root.
       Tiles created later already inherit the root's priority.
     - **Frontier:** the backlog goes first. The feeder's own frontier expands
       only when the backlog is empty (or behind a setting, if both should
       run).
     - **Commands:** `continuous_campaign.py backlog add|move|remove|list`
       (and `set` for the two settings), each taking the lock, so the
       terminal and the dashboard share one meaning.
   - **Dashboard plan once this exists:** a drag-to-reorder list on the Feeder
     page that edits `backlog` and the active order under `pipeline.lock`. The
     dashboard needs no release loop of its own, so releasing keeps working
     when the dashboard is down.
   - **Until then:** the dashboard offers immediate Submit and per-run
     Priority only.
   - **Related:** items 9, 11 and 12.

2. **A failed root does not stop its live tiles.**
   - **Cause:** when `advance()` marks a root failed (a tile exhausted its
     retries), its running and queued children continue.
   - **Effect:** on 2026-09-30, all nine machines kept computing 103³ tiles for
     a root that had already failed, while other fields' tiles waited.
   - **Possible change:** when a root becomes terminal, cancel or pause its
     queued children and request stops for running ones. Keep finished tiles
     for reuse.
   - **Dashboard workaround:** a problem alert, plus **Cancel leftover tiles**.

3. **No process supervision.**
    - **Observed:** no work ran from about 03:50 to 08:41 on 2026-09-30, until
      the leader was restarted, and nothing raised an alert. On 2026-10-02 a
      Wi-Fi roaming failure took five workers offline for about 4.5 hours the
      same way (`docs/NETWORK_OUTAGE_2026-10-02.md`; no fix applied yet).
    - **Today:** `cloudflared` runs as a systemd service, and pellinore has a
      network watchdog (`cluster/ops/`). The leader, feeder, dashboard and
      agents are started by hand or by scripts, and nothing restarts them.
    - **Possible change:** run the leader, feeder and dashboard as systemd user
      services with `Restart=on-failure`, and the agents under a supervisor, so
      the problems feed would mainly show why a restart happened.

## Scheduling and lifecycle

4. **A cancelled tile under a live root is never rescheduled.**
   - **Cause:** `dp_solver/distributed.py` `advance()` skips any tile slot that
     already has a child run (`if row["child_run_id"]: continue`). Since the
     per-tile retry work (item 5), a *failed* tile is reset and retried, but a
     *cancelled* one is still never recreated.
   - **Effect:** cancelling one tile leaves its root `waiting` forever, and the
     leader API cannot recover it, because `resume` accepts only paused runs.
   - **Possible change:** add a `requeue` run command for tiles, which replaces
     a cancelled or failed child with a fresh queued one. Or have `advance()`
     treat cancelled children like failed ones.
   - **Dashboard workaround:** cancelling a single tile of an active root is
     refused. **Restart field** cancels the attempt and submits a new one, which
     reuses every durable tile through `reuse_tiles`.

5. **Tile failures can still fail whole fields (partly addressed).**
   - **Observed:** 97³, 101³ and 103³ each failed all three attempts with
     `distributed_solver.py: timed out` on individual tiles.
   - **Done so far:**
     - `advance()` now retries a failed tile within the same root, up to three
       times with a 30 s to 5 min backoff (`distributed_tile_retries`), instead
       of failing the root at the first failure.
     - Those `timed out` errors most likely come from the 5-second socket
       timeout on blob downloads, not from compute time, and edge bands (item 20)
       cut a tile's download from about 65 MiB to about 1.5 MiB. That should
       remove most of them, but it hasn't been observed on the live campaign.
   - **Still open:** make the timeouts scale with the work (halo size grows
     with p²) and be configurable per field, and record which phase failed
     (fetching, computing or publishing) so the cause is visible (item 6).

6. **Per-lease errors are lost.**
   - **Cause:** `lease_history` records an outcome (`engine retry`, `fail`, …)
     but no message, and `runs.error` is overwritten by later attempts.
   - **Possible change:** add an `error` column to `lease_history`.

## Disk space: what is left

Items 22–24 (completed, below) keep the disks from filling with dead tiles. What
remains is checking space before admitting work.

7. **The leader and feeder only partly act on worker free disk (partly addressed).**
    - **Done so far:**
      - The leader now has a free-space floor (`disk_floor_bytes`, default
        10 GiB; `0` turns it off). A machine below it gets no new leases and no
        new replication copies, shows "low disk" as its idle reason, and keeps
        its running work, its stored-bytes checks and its garbage collection, so
        it can free space. A machine whose agent has never reported free space
        (stored as -1) is not blocked, which is different from reporting 0.
      - The feeder already checks worker free disk when adding roots (see item
        8).
      - The Fleet tab shows each machine's disk in four parts, measured directly
        over SSH (DESIGN.md), and the Submit preview compares a field with it.
    - **Observed (before):** at the last heartbeats on 2026-10-02, dp-102,
      dp-104, dp-105 and dp-151 reported 0 bytes free, with 13⁹ partway through.
    - **Still open:**
      - A lease that is already running is not interrupted when the disk fills.
      - The floor is a fixed size, not a share of the disk.
      - Agents don't report the filesystem's size or what the campaign uses;
        the dashboard measures that itself, so this is low value now.

8. **Admission does not account for disk, except in two places (partly addressed).**
    - **Done so far:**
      - The dashboard's Submit preview estimates a field's tile storage from the
        bytes per DP cell measured on finished fields (their sizes outlive the
        tiles), with a 25% margin and three copies. It compares that with the
        free disk above each machine's floor, minus what fields in progress
        still need. It warns above half and refuses if the field cannot fit.
      - The feeder reserves the remaining storage of active roots and holds back
        fields that would not fit (`replenish_dp`).
    - **Still open:**
      - The feeder reserves 3 copies × 12 bytes per DP cell, but tiles store
        about 1 byte per cell, so it holds back about four times too much and
        may block fields that would fit. It should use the measured rate.
      - `launch_dp.py extend` and the terminal bypass the check entirely.
      - The backlog (item 1) should check disk on release.

## Feeder control

9. **The feeder has no command interface.**
   - **Effect:** changing its limits means editing `pipeline.json`, and
     retrying a field it gave up on means appending to `manifest.json`. There
     is no "restart the feeder" command; `restart_owned_feeder` is only
     reachable from `upgrade-workers`.
   - **Possible change:** feeder subcommands such as `set`, `retry FIELD` and
     `restart`, that take `pipeline.lock` and validate their input.
   - **Dashboard workaround:** the dashboard does these edits itself, under
     `pipeline.lock`, using `validate_settings`.

10. **`launch_dp.py extend` rewrites `manifest.json` without `pipeline.lock`.**
   - **Effect:** it can race the feeder, which saves the manifest during its
     passes, and one update can be lost.
   - **Possible change:** take the lock in `extend`.
   - **Dashboard workaround:** it holds the lock while `extend` runs.

11. **The visit limits in `pipeline.json` and the limits already used disagree
   (needs a re-check).**
   - **Observed:** 89³–113³ were submitted with `max_visits` 2×10¹⁴ (one with
     10¹⁸), but `pipeline.json` said 3×10¹³, and the feeder's next-field list
     was empty.
   - **Changed since:** the pipeline now has a separate `frontier_max_visits`
     (2×10¹⁴) for choosing fields, next to `max_visits` (3×10¹³), which roots it
     submits get. `launch_dp.py extend` still records its limit only in
     `manifest.json`. Today the next-field list is empty again, but the feeder's
     last pass stopped at its disk watermark, so this isn't conclusive.
   - **Possible change:** have `extend` record its limit in the pipeline
     settings, or have the feeder report the limit actually in force.

12. **The tile layout limits are fixed or implicit.**
    - **Cause:** `distributed.create()` caps a root at `max_tiles` (default
      10,000). `scheduling.regional_campaign()` accepts it as an argument, but
      the feeder never sets it, and it always uses its single `tile_side` (512).
    - **Effect:** large fields need a bigger tile side, and large primes have
      tile halos too big for 2 GiB tiles; 127³ fits no layout at all.
    - **Possible change:** choose the tile side per field when submitting, as
      the dashboard's submit command does, and make `max_tiles` a feeder
      setting.

## Observability

13. **Logs have no timestamps.**
    - **Cause:** `leader.log` is mostly Python tracebacks, and `feeder.log` is
      one JSON object per pass.
    - **Possible change:** prefix every line with an ISO time, and emit
      structured JSON events: lease granted or finished, root failed,
      reconcile summary.
    - **Dashboard workaround:** it stamps lines with the time it first saw them.
      Those times live only in the dashboard's memory. After it restarts,
      everything already in the logs becomes an untimed "before watching"
      baseline again, and the leader baseline covers only the current leader
      session. Timestamps written by the leader and feeder themselves would make
      the Problems history exact and permanent.

14. **`/v1/status` is slow (about 6 s).**
    - **Cause:** `DPAdapter.augment_status` runs one replica-count query per
      tile.
    - **Possible change:** use one grouped query, as the dashboard does in
      about 0.1 s.

15. **The running code is not visible.** The feeder records only its process id,
    start time and command (`feeder_process.json`), and agents report a runtime
    version, but the feeder and leader report none. That is how a feeder that
    predated the working tree kept running. **Possible change:** record a code
    hash in `feeder_process.json` and in the leader's `/v1/status`.

16. **`database is locked` errors.**
    - **Observed:** about 44 in one leader session, at `BEGIN IMMEDIATE` in
      `dispatch_post`. They appear to cluster with failure and retry storms,
      not with dashboard reads.
    - **Possible change:** check how long write transactions are held,
      especially in the scheduler loop and in `advance()`, which works through
      every waiting root inside a single transaction.
    - **Added load to watch (2026-10-02):** the finished-tile retirement scan
      runs inside `advance()` (about 0.2 s per batch of 500 packets, and
      back to back while a large backlog clears), and each agent's garbage plan
      takes about 40 ms. Agents pause one second between full batches to keep
      this bounded, but it is worth watching after the first rollout.

17. **`campaigns/result_table.py` once aborted on one uncollected artifact (may
    be moot).**
    - **Observed:** `capacity-canary` had a completed 2³ DP that was never
      collected. That deployment was deleted on 2026-10-02, so it can't be
      reproduced, and the table now shows unfinished fields as "—".
    - **Possible change:** confirm that an uncollected artifact is reported and
      skipped, and close this.

## Operations and security

18. **The leader has no authentication and listens on 0.0.0.0 (accepted for now).**
    - **Effect:** any device on the home network can stop dispatch or cancel
      runs, bypassing the dashboard's login. The dashboard and Cloudflare Access
      protect only the internet-facing side.
    - **Decision (2026-10-02):** left as is; the home network is trusted.
      Binding to localhost is not an option, because the other nine machines
      reach the leader over the network.
    - **Possible change:** require a shared token on mutating routes, carried by
      the agents, feeder, dashboard and `kh.py`, and bind the leader to the LAN
      interface only. Do this before the leader faces anything beyond the home
      network.

---

# Completed items

## Scheduling

19. **A finished root's reconstruction could be starved by other fields' tiles. Fixed.**
   - **Was:** once every tile is durable, `advance()` queues the root for
     reconstruction, and the leader ordered the queue by priority and then by
     *estimated seconds*, where a root keeps its whole-field estimate. On
     2026-10-01, 101³'s root (about 12 days) sat behind every 7¹¹ tile (about
     13 hours each), and it needs an exclusive host, which is free only in the
     instant between two tiles.
   - **Fixed in the leader** (commit "Cluster work on multi-match", 2026-10-01):
     the lease order now puts reconstructing roots first, and
     `reconstruction_drain_target` leaves one machine's freed slots idle until
     its tiles finish, so the exclusive host becomes available. Confirmed in the
     code, not re-observed on a live campaign. The dashboard's alert and
     **Priority…** button remain as a fallback.

20. **Distributed DP re-downloaded whole predecessor tiles. Fixed (not yet deployed).**
    - **Was:** an interior 4096-square tile fetched its top, left and top-left
      predecessors as complete packets (about 65 MiB for 13⁹), although its halo
      reads only a thin border (`docs/DP_NETWORK_LOCALITY.md`).
    - **Done:** each finished tile also publishes up to three small compressed
      edge bands (`bottom`, `right`, `corner`; about 1 MiB in all), indexed by
      packet hash in `tile_bands`. A successor fetches one band per predecessor.
      On a real 13⁹ tile the inputs shrink from 65.1 MiB to 1.47 MiB (44× less)
      with a byte-identical halo. Any band problem falls back to the whole
      packet, old and reused tiles simply have no bands, and `KH_DP_BANDS=0`
      turns it off. GPU tiles use and publish bands the same way.
    - **Measure:** each tile's progress record carries `input_mode`,
      `input_bytes`, `input_band_bytes` and `bands_published`; the query is in
      the locality document.

21. **Tiles were placed without regard to where their inputs were. Done (not yet
    deployed; small benefit).**
    - **Done:** when a machine asks for work, the leader prefers a tile whose left
      neighbour that machine produced or stores, as a tie-break inside the same
      root and priority. A tile that has waited 15 minutes ignores it, and
      `KH_ROW_AFFINITY=0` turns it off.
    - **Value:** after edge bands, the left neighbour's band is under 1% of the
      original traffic, so this is cheap but modest.

## Disk space

Measured on 2026-10-02 before any cleanup, from the leader's database and `du` on
each machine, almost all campaign disk was DP tile packets in each agent's
`…/dp-<deployment>/blobs/`:

- **Unique tile data:** 235 GB, about one byte per DP cell, roughly
  (p^(⌊r/2⌋+1))² bytes per field.
- **Stored:** 1.66 TB, because each tile sat on 5–10 machines (7 on average)
  against a target of 3.
- **Finished fields** accounted for 553 GB of it; **unfinished ones** (13⁹,
  23⁷) for 1.1 TB.
- Final results are tiny: a KHD1 split is kilobytes, and all 62 matching
  results came to 816 MB.

**Items 22–24 are implemented** (not yet deployed). Replayed against a copy of the
live database, retiring finished fields and trimming surplus copies take the
indexed tile data from 1,502 GiB to 430 GiB, with every remaining artifact at
three copies or fewer. The same logic was applied by hand to the live data while
the campaign was stopped, and **freed 1.23 TB** (merlin went from 11 GiB to
282 GiB free). The causes below are kept for the record.

22. **Tile packets were never deleted, even after their field was finished.**
    - **Cause:** garbage collection (`cluster/retention.py`, `collect_plan`)
      protects every row of `artifacts`. Only retired checkpoint objects were
      ever deletable.
    - **Effect:** 11⁹ (finished and matched) held 193 GB, 7¹¹ 128 GB, 19⁷ 123 GB
      and 17⁷ 63 GB. The tiles of failed attempts stayed as well, even once a
      later attempt had finished.
    - **Edge bands:** each tile also has up to three small band artifacts
      (item 20), retired together with their packet.
    - **Implemented:** `distributed.retire_finished_tiles`, run from the
      scheduler's `advance` (even while dispatch is stopped, at most every two
      minutes). A packet is retired when every root that references it is
      complete, or is a failed or cancelled attempt of a field that has a
      complete root, and that complete root finished at least
      `tile_retention_seconds` ago (default 6 hours) with its result held on two
      live nodes. A waiting, queued, running or paused root keeps every packet
      it uses, including ones shared through `reuse_tiles`. Retired replicas
      leave the index at once, so nothing is sent to them, and each holder
      deletes the blob through the garbage-collection route (`artifact_trim`,
      see item 23). `UPDATE settings SET value='-1' WHERE
      key='tile_retention_seconds'` keeps tiles forever; a larger number keeps
      them longer. On the live database it retires 43,196 packets (464 GiB of
      unique data, 305,070 blob copies, all 67 finished fields) and leaves 13⁹
      and 23⁷ alone.
    - **Not covered:** the tiles of a failed field with no completed attempt
      (for example 107³) are kept, because a retry can reuse them.

23. **Copies piled up beyond `target_replicas`.**
    - **Cause:** the leader's replication query (`leader.py`, route
      `/v1/replication`) counted only copies on nodes whose last heartbeat was
      under `lease_seconds` (60 s) old. Whenever an agent missed heartbeats for
      a minute (a Wi-Fi drop, an agent restart, a rolling upgrade), each of its
      tiles looked short of copies, and another machine made a new one. When the
      agent returned, its copy counted again, and nothing removed the surplus.
    - **Effect:** every tile was at target 3, but they had 5–10 copies. 13⁹'s
      139 GB of tiles occupied 1 TB.
    - **Implemented:**
      - *Grace period:* the replication query counts copies on nodes that sent
        a heartbeat within `replica_grace_seconds` (default 600, never less than
        the lease window), so a Wi-Fi drop or restart no longer makes the
        leader copy everything the node held. Readers and durability checks
        still use only live nodes. `UPDATE settings SET value=... WHERE
        key='replica_grace_seconds'` changes it.
      - *Trimming:* `retention.excess_plan`. When an agent asks for its garbage
        plan, the leader ranks the healthy holders of each artifact by free
        disk (least first) and has the first *holders − target* of them drop
        their copy. At least `target_replicas` copies always remain on healthy
        machines, copies younger than 10 minutes are left alone, and a copy is
        removed from the index at once and queued in `artifact_trim` until the
        agent deletes the blob and acknowledges (`/v1/gc-done`). A blob that is
        also a retained checkpoint member is kept. Scans run when the queue
        is empty (at most every five minutes, or at once after a full batch of
        2,000) and queue up to 2,000 copies, so later plans only read the queue.
      - *Agents* now repeat garbage collection one second apart while batches
        are full, instead of once a minute, so a backlog clears in minutes.
    - **Measured on a copy of the live database:** 1,072 GiB deleted across the
      cluster (finished fields plus surplus), the least-free machines first
      (dp-151 190 GiB, dp-105 163, dp-104 162, dp-102 139); 3,800 plan calls at
      about 38 ms each of leader time.

24. **Run scratch directories were left behind.**
    - **Cause:** agents deleted a run's work directory contents only through
      the adapter's `cleanup` hook, after durable completion. The DP adapter
      removed only `tile-output`, `tile-inputs`, `reconstruction` and
      `halo.bin`. Failed, cancelled and stopped runs kept everything, and the
      matching adapter's `phase-state/` and `result.bin` (up to several GB
      each) were never removed.
    - **Observed:** 10,294 run folders and 67 GB under merlin's `…/work/`,
      and 3–28 GB on each worker.
    - **Implemented:**
      - *After a lease ends:* the agent deletes the run's scratch directory
        (`work/<run>/<lease>`, and the run directory once empty) after the result
        is stored and completed. Tile and root runs (`retry_elsewhere`) are also
        deleted when they fail, stop or lose their lease, since they are
        recomputed rather than resumed locally. `KH_KEEP_SCRATCH=1` in the agent's
        environment keeps everything, for debugging.
      - *Sweep:* each agent, at start and then hourly, lists the run
        directories under its work root that have been idle for an hour and asks
        the leader (`/v1/work-sweep`) their states. It deletes those of complete,
        failed and cancelled runs, and those of runs the leader has never heard of
        once a day old, 200 at a time. Queued, running and paused runs are kept,
        and so is the shared `.dependency-cache`.
      - This supersedes `cluster/ops/cleanup_redundant_work.py`.
