# Campaign library: changes that would give better control

These notes come from building the dashboard. Each item describes a
limitation in the campaign code (leader, feeder, launcher, solvers), what it
causes in practice, and a possible change. The dashboard works around some of
them; the workarounds are listed so they can be removed once the library
changes. None of these have been made: the dashboard leaves campaign code
untouched.

Status as of 2026-10-01.

## First priority

Suggested order of work: **1** (feeder backlog), then **3** (a failed root
stops its tiles), **4** (tile timeouts), **6** (reconstruction starvation),
**15** (worker disk) and, before the leader faces anything beyond the home
network, **18** (leader authentication).

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

15. **The leader cannot see free disk on the workers.**
    - **Effect:** a large field such as 11⁹ (about 290 GiB of DP state, kept in
      two tile copies) could fill worker disks partway through. Nothing
      reports or prevents that in advance.
    - **Possible change:** agents report free and used space on their
      storage roots in heartbeats. Admission of a new root then checks its
      projected tile storage.
    - **Dashboard workaround:** the submit preview states the disk the field
      will need, but cannot compare it with what is free.

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
