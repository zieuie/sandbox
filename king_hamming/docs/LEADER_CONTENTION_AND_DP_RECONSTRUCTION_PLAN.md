# Leader contention and completed-DP reconstruction

Planning snapshot: 2026-10-03. This is a plan, not a live-campaign change.

## Current state and what changed

- `4d82f8a` closes leader SQLite connections promptly and raises its file-descriptor
  limit. This fixes the connection/descriptor leak under contention, not the
  contention itself.
- `2a9cf1f` adds exact compact tile format 2, a GPU-size guard, bounded scans
  for roots over 10,000 tiles, and tile-phase timings. `055b245` shares tile
  planning between feeder and dashboard and adds feeder settings. `101ecc8`
  documents the storage work and adds a band-only reconstruction prototype;
  the prototype is not part of the deployed solver.
- The retained rollout completed at 13:26 CDT on 2026-10-03. All 11 nodes
  reported runtime `9028bd557a0a34c43eb6a8c6f07074c341529c69add1248b164173e9e486f397`.
  The feeder is running with `tile_format=2` and `max_tiles=100000`. Existing
  13^9 attempts retain format 1; the new 7^13 root uses format 2. Some earlier
  markdown still says these changes are undeployed and should be updated later.
- All three 13^9 roots failed in reconstruction with `distributed_solver.py:
  timed out`. Each still has all 8,281 child tiles complete and, at inspection,
  at least two live replicas per tile. The feeder's three-attempt cap is now
  exhausted for that field. Recomputing the DP is unnecessary.
- The leader still logged `database is locked` at `dispatch_post()`'s
  `BEGIN IMMEDIATE` after the rollout. During the incident, dp-104 and dp-156
  reported leader timeouts for heartbeats, replication, and leasing as well as
  reconstruction. `/v1/health` called the scheduler healthy even during this
  broader request failure. One snapshot showed over 100 leader threads, most
  sleeping in SQLite's busy wait. The scheduler optimization in `a8725ad`
  reduced one DP pass from about 1.0 s to 0.17 s, but did not solve bursts.

## Fix in order

1. **Measure actual lock ownership and request delay.** Record per-route time
   waiting to enter `BEGIN IMMEDIATE` and time holding the transaction; record
   scheduler/adapter phase durations, active HTTP handlers and lock timeouts.
   Put bounded aggregates in `/v1/health` or status, not per-request log spam.
   First locate the longest writer transactions on a private database copy,
   then confirm with live metrics. A healthy scheduler tick must not imply
   healthy worker requests.
2. **Keep reads out of the writer queue.** `/v1/tile-input` without
   `publish_bands` reads a fenced lease and immutable artifact descriptors; it
   should use a short read transaction, with explicit token, state, and expiry
   checks. The `publish_bands` variant still writes. Audit other read-only POST
   paths similarly. Stop running global `recovery.expire()` for every request;
   let the scheduler own periodic expiry, and require route-local expiry checks
   where fencing matters. Keep `BEGIN IMMEDIATE` for lease assignment and
   state changes. Set WAL mode once at initialization rather than on each new
   connection if profiling confirms that PRAGMA adds contention.
3. **Bound every writer transaction.** Move finished-tile retirement and large
   root scans into small, independently committed batches, preserving the
   existing priority of reconstruction over tile leases. Avoid holding one
   transaction through every waiting root. Ensure retries/reuse do not copy
   thousands of complete child rows simply to retry reconstruction.
4. **Make reconstruction transport failures non-terminal.** A timed-out input
   descriptor request or peer fetch should retry with bounded backoff while
   the agent's independent lease keeper renews ownership. Do not count that as
   an engine failure. Add a guarded way to requeue only a failed root's
   reconstruction when all its original tiles are still complete and durable:
   clear its old lease/error, retain `distributed_tiles` and artifacts, and
   return it to reconstruction priority. The feeder must not burn a new
   mathematical attempt for this class of failure. Preserve the three-attempt
   cap for genuine deterministic solver failures.
5. **Prove behavior under load and failure.** Tests should hold a writer lock
   across concurrent heartbeats and tile-input calls, inject HTTP timeouts
   during reconstruction, and verify no tile is dropped, no second root is
   created, and a stale token is refused. Run a small end-to-end format-1 and
   format-2 field, then load-test the leader with the current number of agent
   slots. Keep GPU disabled in test agents as the new test fixtures do.

## Rollout and acceptance

Do not stop active tiles just to replace the leader: campaign `stopped` is also
a `stop_requested` signal to running solvers. Deploy a leader-only change using
an owned-process, lease-slack-checked restart when the request backlog is calm;
workers need no replacement unless agent retry behavior changes. For an agent
change, use the retained guarded worker upgrade only after work quiesces, or a
separately designed rolling upgrade that preserves leases. Keep a recoverable
database backup and the exact deployed runtime identities.

After rollout, verify all 11 nodes and the feeder, no new SQLite lock errors or
lease losses under normal load, bounded request latency, and completion of 13^9
from its existing 8,281 tiles. The current three failed roots are evidence and
recovery inputs; do not delete them or their blobs.
