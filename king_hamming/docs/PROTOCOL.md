# Solver supervision protocol

The first draft uses `/v1/` JSON HTTP control messages and newline-delimited JSON
on solver stdout. Solver diagnostics go to stderr. The agent drains both streams
while polling control; a quiet solver must never block a stop request.

## Solver reports

A C DP status line looks like:

```json
{
  "done": 1200,
  "total": 1771561,
  "checkpoint_done": 0,
  "checkpoint_tiles": 0,
  "threads": 4,
  "units": "cells",
  "phase": "computing",
  "message": "dp cells",
  "heartbeat": true
}
```

`done` counts completed positive-budget cells, including work in the current
uncommitted tile. A diagonal is counted only after all workers finish it.
`checkpoint_done` counts cells covered by the durable **local** restart cursor;
it does not assert central replication. Counts satisfy
`0 <= checkpoint_done <= done <= total`.

Reports arrive every ten seconds by default even when `done` is unchanged. The
C reporter runs independently of the pool and emits additional snapshots at phase
and checkpoint changes. `--progress-milliseconds` controls report frequency;
`--checkpoint-seconds` separately controls durability. Progress does not force a
checkpoint or make partial tile state restartable.

Phases are `computing`, `checkpointing`, `snapshotting`, `reconstructing`, and `stopped`. A stop
finishes the current tile, flushes arrays, publishes the cursor, emits the stopped
snapshot, and exits 75. Transition construction and initial state setup currently
precede reporter startup and appear as `starting` in the leader.

The demo solver uses steps rather than cells and checkpoints before each report.
Reports that omit `checkpoint_done` therefore default to `done` for compatibility
with that demonstration contract.

## Independent control

The agent sends `/v1/run-control` with `run_id` and `lease_token` every second by
default. A valid response includes `campaign_state` and `stop_requested`.
The agent sends SIGTERM to the solver process group when stopping is requested,
continues draining progress and diagnostics, and waits for the exit boundary.
It does not restart a deliberately stopped solver.

`stop --all` persists a stop flag on every active lease. `resume --all` enables
new dispatch but does not erase those flags. The old lease must quiesce and be
requeued before a new lease clears its stop flag. This prevents a quick
stop/resume from being missed between control polls.

A child that completes with exit zero retains its completed artifact even if a
stop arrived during final work. Exit 75 after an intentional signal returns the
job to the queue. If the child ignores the signal, `--stop-grace-seconds` defaults
to 1800; expiry sends SIGKILL and retains the previous committed checkpoint.
A checkpoint error while stopping remains a failed run and is not automatically
retried. An ordinary unrequested child failure still permits one checkpoint
restart.

`--control-seconds` controls the polling interval. Network request latency can
extend it. Each partial stdout record is limited to 1 MiB, and diagnostic memory
retains only the last 64 KiB of stderr. Cleanup terminates the child process group
on protocol errors or an interrupt of the supervising call.

## Leader status

The leader stores three separate timestamps:

- `last_solver_heartbeat`: the latest genuine solver report, including unchanged
  counters;
- `last_progress_at`: the latest increase in computed work;
- `last_checkpoint_at`: the latest increase in durable local checkpoint coverage.

A missing initial timestamp is displayed as unknown. `status` also shows units,
phase, computed work, durable work, heartbeat age, and checkpoint age. The initial
zero-cell checkpoint has no coverage increase and therefore no checkpoint-age
sample yet.

Solver health distinguishes starting, responding, stopping, missing heartbeat,
five-minute no-progress warning, and thirty-minute stall. Node heartbeats remain
separate: a healthy agent cannot certify a stalled solver as healthy. These
health labels are diagnostic; automatic stall escalation and machine reboot
remain subsequent recovery work.

Schema upgrades add supervision columns to existing databases without deleting
run history. Terminal, expired and reassigned leases reject stale updates.
Checkpoint transfer and expired-lease reassignment are now implemented. The
original `checkpoint_done` counter still asserts only local durability; additional
replication and restore counters are described in [`RECOVERY.md`](RECOVERY.md).

## Recovery messages

`/v1/register` binds a node name to a new session UUID; `storage_only` disables
computation leasing while retaining storage and replication. `/v1/heartbeat`
requires that incarnation. `/v1/lease` grants one computation per worker.
`/v1/run-control` renews a valid computation lease as well as returning stop state.

`/v1/checkpoint` publishes a validated manifest under a valid lease.
`/v1/replication` returns missing checkpoint or artifact copies;
`/v1/checkpoint-replica` acknowledges a complete verified snapshot.
`/v1/blob-bad` removes confirmed damaged replica claims. `/v1/recovery` supplies
ordered available snapshots, and `/v1/restored` records the installed snapshot
and acknowledges its verified local copy. Protected computation messages require
current run/lease ownership; replication acknowledgments require a current node
session. Updates are validated and applied within one write transaction.

The solver's checkpoint event is an agent handshake rather than a progress record.
One newline acknowledges successful capture and publication; EOF or a protocol
error fails the solver. Control and heartbeat processing continue during capture.
The leader's reaper expires unrenewed leases even without new dispatch requests.
