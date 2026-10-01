# Campaign library: changes that would give better control

These notes come from building the dashboard. Each item describes a
limitation in the campaign code (leader, feeder, launcher, solvers), what it
causes in practice, and a possible change. The dashboard works around some of
them; the workarounds are listed so they can be removed once the library
changes. None of these have been made: the dashboard leaves campaign code
untouched.

Status as of 2026-09-30.

## Scheduling and lifecycle

1. **A cancelled tile under a live root is never rescheduled.**
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

2. **A failed root does not stop its live tiles.**
   - **Cause:** when `advance()` marks a root failed, its running and queued
     children continue.
   - **Effect:** on 2026-09-30, all nine machines kept computing 103³ tiles for
     a root that had already failed, while other fields' tiles waited.
   - **Possible change:** when a root becomes terminal, cancel or pause its
     queued children and request stops for running ones. Keep finished tiles
     for reuse.
   - **Dashboard workaround:** a problem alert, plus **Cancel leftover tiles**.

3. **Tile timeouts fail whole fields.**
   - **Observed:** 97³, 101³ and 103³ each failed all three attempts with
     `distributed_solver.py: timed out` on individual tiles.
   - **Effect:** retries reuse finished tiles, but they hit the same timeout
     again.
   - **Possible change:** scale the tile timeout with predicted tile work
     (halo size grows with p²), and make it configurable per field. Record
     which phase timed out (fetching, computing or publishing), so the cause is
     visible.

4. **Per-lease errors are lost.**
   - **Cause:** `lease_history` records an outcome (`engine retry`, `fail`, …)
     but no message, and `runs.error` is overwritten by later attempts.
   - **Possible change:** add an `error` column to `lease_history`.

## Feeder control

5. **The feeder has no command interface.**
   - **Effect:** changing its limits means editing `pipeline.json`, and
     retrying a field it gave up on means appending to `manifest.json`. There
     is no "restart the feeder" command; `restart_owned_feeder` is only
     reachable from `upgrade-workers`.
   - **Possible change:** feeder subcommands such as `set`, `retry FIELD` and
     `restart`, that take `pipeline.lock` and validate their input.
   - **Dashboard workaround:** the dashboard does these edits itself, under
     `pipeline.lock`, using `validate_settings`.

6. **`launch_dp.py extend` rewrites `manifest.json` without `pipeline.lock`.**
   - **Effect:** it can race the feeder, which saves the manifest during its
     passes, and one update can be lost.
   - **Possible change:** take the lock in `extend`.
   - **Dashboard workaround:** it holds the lock while `extend` runs.

7. **The visit limit in `pipeline.json` and the limits already used disagree.**
   - **Observed:** 89³–113³ were submitted with `max_visits` 2×10¹⁴ (one with
     10¹⁸), but `pipeline.json` says 3×10¹³.
   - **Effect:** the feeder's next-field list is empty and the frontier looks
     exhausted.
   - **Possible change:** have `extend` record its limit in the pipeline
     settings, or have the feeder report the limit actually in force.

## Observability

8. **Logs have no timestamps.**
   - **Cause:** `leader.log` is mostly Python tracebacks, and `feeder.log` is
     one JSON object per pass.
   - **Possible change:** prefix every line with an ISO time, and emit
     structured JSON events: lease granted or finished, root failed,
     reconcile summary.
   - **Dashboard workaround:** it stamps lines with the time it first saw them.

9. **`/v1/status` is slow (about 6 s).**
   - **Cause:** `DPAdapter.augment_status` runs one replica-count query per
     tile.
   - **Possible change:** use one grouped query, as the dashboard does in
     about 0.1 s.

10. **`campaigns/result_table.py` aborts on one uncollected artifact.**
    - **Observed:** `capacity-canary` has a completed 2³ DP that was never
      collected.
    - **Possible change:** report the field as uncollected and continue.

11. **The running code is not visible.** The feeder that has been running
    since 2026-09-29 predates the working tree. Agents report a runtime
    version, but the feeder and leader do not. **Possible change:** record a
    code hash in `feeder_process.json` and in the leader's `/v1/status`.

12. **`database is locked` errors.**
    - **Observed:** about 44 in one leader session, at `BEGIN IMMEDIATE` in
      `dispatch_post`. They appear to cluster with failure and retry storms,
      not with dashboard reads.
    - **Possible change:** check how long write transactions are held,
      especially in the scheduler loop and in `advance()`, which works through
      every waiting root inside a single transaction.

## Operations and security

13. **No process supervision.**
    - **Observed:** no work ran from about 03:50 to 08:41 on 2026-09-30, until
      the leader was restarted, and nothing raised an alert.
    - **Possible change:** run the leader, feeder and dashboard as systemd user
      services with `Restart=on-failure`. The dashboard's problems feed would
      then mainly show why a restart happened.

14. **The leader has no authentication and listens on 0.0.0.0.**
    - **Effect:** any device on the home network can stop dispatch or cancel
      runs.
    - **Possible change:** bind it to the LAN interface only, and require a
      shared token on mutating routes, which the agents, feeder and dashboard
      would carry.
