# Campaign library: changes that would give better control

These notes come from building the dashboard. Each item describes a
limitation in the campaign code (leader, feeder, launcher, solvers), what it
causes in practice, and a possible change. The dashboard works around some of
them; the workarounds are listed so they can be removed once the library
changes. None of these have been made: the dashboard leaves campaign code
untouched.

Status as of 2026-10-02.

## First priority

Suggested order of work: **1** (feeder backlog), then **3** (a failed root
stops its tiles), **4** (tile timeouts), **6** (reconstruction starvation),
**15** and **19–23** (disk space; see below, since the disks filled on
2026-10-02) and, before the leader faces anything beyond the home network,
**18** (leader authentication).

1. **The feeder should own an ordered backlog of fields.**
   - **Today:**
     - The feeder adds new roots only from `scheduling.campaign()`, in raw-visit
       order, within `target_dp_roots` / `max_dp_roots`.
     - Fields an operator chooses (the dashboard's Submit, or
       `launch_dp.py extend`) bypass it and start at once at priority 0.
     - The leader then orders equal priorities by estimated runtime, so
       whichever field is smallest runs first. That is what starved 101³'s
       reconstruction behind 7¹¹ (item 6).
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
       below `backlog_active`, and the disk watermark allows, it pops the
       first entry, builds the specification, enqueues it and records it in
       the manifest, all within the lock it already holds.
     - **Tile side:** choose it per field, as the dashboard's `tile_plan()`
       does (11⁹ needs 2048, not 512). That function belongs in `dp_solver`,
       so the feeder and dashboard share one copy (see item 16).
     - **Order becomes priority:** active backlog roots, then waiting entries,
       take priorities by position, top first. Matching stays above them at
       `matching_priority` (100), and the feeder's own frontier stays at 0.
       When the order changes, the feeder updates each affected root and its
       already-queued tiles. A leader run command that reprioritizes a root
       together with its queued children would make that one call per root.
       Tiles created later already inherit the root's priority, and so does
       its reconstruction, which also fixes item 6 for backlog fields.
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
   - **Related:** items 6, 7, 9 and 16.

## Scheduling and lifecycle

2. **A cancelled tile under a live root is never rescheduled.**
   - **Cause:** `dp_solver/distributed.py` `advance()` skips any tile slot that
     already has a child run (`if row["child_run_id"]: continue`). It only fails
     the root when a child *failed*.
   - **Effect:** cancelling one tile leaves its root `waiting` forever, and the
     leader API cannot recover it, because `resume` accepts only paused runs.
   - **Possible change:** add a `requeue` run command for tiles, which replaces
     a cancelled or failed child with a fresh queued one. Or have `advance()`
     treat cancelled children like empty slots.
   - **Dashboard workaround:** cancelling a single tile of an active root is
     refused. **Restart field** cancels the attempt and submits a new one, which
     reuses every durable tile through `reuse_tiles`.

3. **A failed root does not stop its live tiles.**
   - **Cause:** when `advance()` marks a root failed, its running and queued
     children continue.
   - **Effect:** on 2026-09-30, all nine machines kept computing 103³ tiles for
     a root that had already failed, while other fields' tiles waited.
   - **Possible change:** when a root becomes terminal, cancel or pause its
     queued children and request stops for running ones. Keep finished tiles
     for reuse.
   - **Dashboard workaround:** a problem alert, plus **Cancel leftover tiles**.

4. **Tile timeouts fail whole fields.**
   - **Observed:** 97³, 101³ and 103³ each failed all three attempts with
     `distributed_solver.py: timed out` on individual tiles.
   - **Effect:** retries reuse finished tiles, but they hit the same timeout
     again.
   - **Possible change:** scale the tile timeout with predicted tile work
     (halo size grows with p²), and make it configurable per field. Record
     which phase timed out (fetching, computing or publishing), so the cause is
     visible.

5. **Per-lease errors are lost.**
   - **Cause:** `lease_history` records an outcome (`engine retry`, `fail`, …)
     but no message, and `runs.error` is overwritten by later attempts.
   - **Possible change:** add an `error` column to `lease_history`.

6. **A finished root's reconstruction can be starved by other fields' tiles.**
   - **Cause:** once every tile is durable, `advance()` queues the root for
     reconstruction. The leader orders the queue by priority, then by
     *estimated seconds*, and a root keeps its whole-field estimate.
   - **Observed:** on 2026-10-01, 101³'s root (estimated at about 12 days) sat
     behind every 7¹¹ tile (about 13 hours each) at equal priority. It also
     needs an exclusive host, which is only free in the instant between two
     tiles.
   - **Effect:** it would wait until 7¹¹ ran out of ready tiles.
   - **Possible change:** when a root enters reconstruction, raise its
     priority above its own field's tiles, or estimate the reconstruction step
     alone.
   - **Dashboard workaround:** a problem alert, plus a **Priority…** button on
     roots.

## Feeder control

7. **The feeder has no command interface.**
   - **Effect:** changing its limits means editing `pipeline.json`, and
     retrying a field it gave up on means appending to `manifest.json`. There
     is no "restart the feeder" command; `restart_owned_feeder` is only
     reachable from `upgrade-workers`.
   - **Possible change:** feeder subcommands such as `set`, `retry FIELD` and
     `restart`, that take `pipeline.lock` and validate their input.
   - **Dashboard workaround:** the dashboard does these edits itself, under
     `pipeline.lock`, using `validate_settings`.

8. **`launch_dp.py extend` rewrites `manifest.json` without `pipeline.lock`.**
   - **Effect:** it can race the feeder, which saves the manifest during its
     passes, and one update can be lost.
   - **Possible change:** take the lock in `extend`.
   - **Dashboard workaround:** it holds the lock while `extend` runs.

9. **The visit limit in `pipeline.json` and the limits already used disagree.**
   - **Observed:** 89³–113³ were submitted with `max_visits` 2×10¹⁴ (one with
     10¹⁸), but `pipeline.json` says 3×10¹³.
   - **Effect:** the feeder's next-field list is empty and the frontier looks
     exhausted.
   - **Possible change:** have `extend` record its limit in the pipeline
     settings, or have the feeder report the limit actually in force.

## Observability

10. **Logs have no timestamps.**
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

11. **`/v1/status` is slow (about 6 s).**
   - **Cause:** `DPAdapter.augment_status` runs one replica-count query per
     tile.
   - **Possible change:** use one grouped query, as the dashboard does in
     about 0.1 s.

12. **`campaigns/result_table.py` aborts on one uncollected artifact.**
    - **Observed:** `capacity-canary` has a completed 2³ DP that was never
      collected.
    - **Possible change:** report the field as uncollected and continue.

13. **The running code is not visible.** The feeder that has been running
    since 2026-09-29 predates the working tree. Agents report a runtime
    version, but the feeder and leader do not. **Possible change:** record a
    code hash in `feeder_process.json` and in the leader's `/v1/status`.

14. **`database is locked` errors.**
    - **Observed:** about 44 in one leader session, at `BEGIN IMMEDIATE` in
      `dispatch_post`. They appear to cluster with failure and retry storms,
      not with dashboard reads.
    - **Possible change:** check how long write transactions are held,
      especially in the scheduler loop and in `advance()`, which works through
      every waiting root inside a single transaction.

15. **Nothing acts on worker free disk.**
    - **Today:** agents report `storage_free_bytes` in every heartbeat
      (`agent.py`, `heartbeat_loop`), but nothing reads it. Neither the leader nor
      the feeder checks it before admitting work, and nothing reports the
      disk's total size or what is using it.
    - **Observed:** at the last heartbeats on 2026-10-02, dp-102, dp-104,
      dp-105 and dp-151 reported 0 bytes free, with 13⁹ (about 1 TB of stored
      tiles) partway through.
    - **Possible change:** agents also report the filesystem's total size and
      the bytes under their deployment directory. Admission of a new root, and
      the feeder's release of one, check its projected tile storage (see item
      23) against the free space, and dispatch pauses tile work below a
      watermark instead of failing writes.
    - **Dashboard workaround:** the submit preview states the disk the field
      will need, but cannot compare it with what is free. DESIGN.md plans a
      disk view on the Fleet tab.

16. **The tile layout limits are fixed or implicit.**
    - **Cause:** `distributed.create()` caps a root at `max_tiles` (default
      10,000), which no setting exposes. The feeder always uses its single
      `tile_side`.
    - **Effect:** large fields need a bigger tile side, and large primes have
      tile halos too big for 2 GiB tiles; 127³ fits no layout at all.
    - **Possible change:** choose the tile side per field when submitting, as
      the dashboard's submit command does, and make `max_tiles` a feeder
      setting.

## Operations and security

17. **No process supervision.**
    - **Observed:** no work ran from about 03:50 to 08:41 on 2026-09-30, until
      the leader was restarted, and nothing raised an alert.
    - **Possible change:** run the leader, feeder and dashboard as systemd user
      services with `Restart=on-failure`. The dashboard's problems feed would
      then mainly show why a restart happened.

18. **The leader has no authentication and listens on 0.0.0.0.**
    - **Effect:** any device on the home network can stop dispatch or cancel
      runs.
    - **Possible change:** bind it to the LAN interface only, and require a
      shared token on mutating routes, which the agents, feeder and dashboard
      would carry.

## Disk space

Measured on 2026-10-02, from the leader's database (as of the last
heartbeats) and `du` on each machine. Almost all campaign disk is DP tile
packets in each agent's `…/dp-<deployment>/blobs/`:

- **Unique tile data:** 235 GB, about one byte per DP cell, roughly
  (p^(⌊r/2⌋+1))² bytes per field.
- **Stored:** 1.66 TB, because each tile sits on 5–10 machines (7 on average)
  against a target of 3.
- **Finished fields** account for 553 GB of it; **unfinished ones** (13⁹,
  23⁷) for 1.1 TB.
- Final results are tiny: a KHD1 split is kilobytes, and all 62 matching
  results come to 816 MB.

Items 19 and 20 together would bring today's 1.66 TB down to about 470 GB.

19. **Tile packets are never deleted, even after their field is finished.**
    - **Cause:** garbage collection (`cluster/retention.py`, `collect_plan`)
      protects every row of `artifacts`. Only retired checkpoint objects are
      ever deletable.
    - **Effect:** 11⁹ (finished and matched) still holds 193 GB, 7¹¹ 128 GB,
      19⁷ 123 GB and 17⁷ 63 GB. The tiles of failed attempts stay as well,
      even once a later attempt has finished.
    - **Possible change:**
      - When a root is complete and its KHD1 result is safely stored (in the
        feeder's manifest and `results/`, and ideally checked by
        `verify_dp.py`), mark its tiles' artifacts as retired. Then queue their
        blobs in `checkpoint_garbage` on each holder, so the existing
        `/v1/gc-plan` path deletes them.
      - Keep the tiles of failed, paused and cancelled roots, since
        `reuse_tiles` needs them, until a later attempt of the same field
        completes.
      - A `tile_retention` setting (`delete`, or keep for N days) leaves room
        to recompute the final reconstruction.
    - **Savings:** about 550 GB today, and each future field's whole tile set
      once it finishes.

20. **Copies pile up beyond `target_replicas`.**
    - **Cause:** the leader's replication query (`leader.py`, route
      `/v1/replication`) counts only copies on nodes whose last heartbeat is
      under `lease_seconds` (60 s) old. Whenever an agent misses heartbeats
      for a minute (a Wi-Fi drop, an agent restart, a rolling upgrade), each of
      its tiles looks short of copies, and another machine makes a new one.
      When the agent returns, its copy counts again, and nothing removes the
      surplus.
    - **Effect:** every tile is at target 3, but they have 5–10 copies. 13⁹'s
      139 GB of tiles occupy 1 TB.
    - **Possible change:**
      - Wait longer before re-replicating: use a separate grace period of
        several minutes, rather than the lease timeout.
      - Have garbage collection trim copies above the target, removing them
        first from the machines with the least free disk. Never trim to below
        the target among currently healthy machines.
    - **Savings:** trimming alone takes 1.66 TB to about 680 GB.

21. **Run scratch directories are left behind.**
    - **Cause:** agents delete a run's work directory contents only through
      the adapter's `cleanup` hook, after durable completion. The DP adapter
      removes only `tile-output`, `tile-inputs`, `reconstruction` and
      `halo.bin`. Failed, cancelled and stopped runs keep everything, and the
      matching adapter's `phase-state/` and `result.bin` (up to several GB
      each) are never removed.
    - **Observed:** 10,294 run folders and 67 GB under merlin's `…/work/`,
      and 3–28 GB on each worker.
    - **Possible change:** remove a run's directory once its result is
      durable (two verified copies) or its run is terminal. Sweep, when an
      agent starts, any directory whose run is not leased to that agent.

22. **Retired deployments keep their storage.**
    - **Observed:** `dp-65de…`, `match-577e…` and `dp-b78f…` under
      `~/.local/share/king_hamming/`, from earlier deployments, hold about
      2–5 GB on each machine.
    - **Possible change:** a `launch_dp.py retire DEPLOYMENT` command that
      confirms the deployment's results are collected, stops its agents, and
      removes its storage on every host.

23. **Admission does not account for disk.**
    - **Effect:** a root's tiles need about (p^(⌊r/2⌋+1))² bytes per copy.
      Finishing 13⁹ needs about 50 GB more of unique tile data, and finishing
      23⁷ about 70–85 GB. At three copies, 23⁷ needs 210–255 GB, and the whole
      cluster had about 360 GB free.
    - **Possible change:** estimate a root's tile storage at submission
      (`tile_plan()` already knows the layout) and refuse or hold the root
      while the cluster's free disk, less a reserve, can't hold
      `target_replicas` copies. Pair this with item 15's watermark, and with
      item 1's disk check on backlog release.
