# Replicated checkpoint recovery

The exact DP can now resume on another worker after its original agent disappears.
One calculation still executes on one machine at a time; this milestone does not
spread that calculation's tiles across machines simultaneously.

## Modules

| Module | Responsibility |
| --- | --- |
| `blob_store.py` | Streaming hashes, immutable publication, resumable downloads |
| `checkpoints.py` | Manifest validation, consistent capture, atomic restore |
| `recovery.py` | Checkpoint index, replica claims, lease history and expiration |
| `agent.py` | Solver supervision, lease renewal, capture and peer replication |
| `leader.py` | Transactional ownership, scheduling and recovery control |
| `cluster_smoke.py` | Explicit, isolated SSH failure experiment |

These modules can be read and reused separately. The leader indexes snapshots;
it does not receive or retain the DP matrices.

## Consistent snapshots

The agent launches the C solver with `--checkpoint-handshake`. After committing
its arrays and cursor, the solver emits:

```json
{"event":"checkpoint","cursor":1}
```

The worker pool remains idle until the agent sends a newline on stdin. During
this pause, the agent copies and hashes `values.bin`, `choices.bin` and
`checkpoint.bin` into immutable worker storage and publishes a manifest to the
leader. Solver heartbeats and campaign control continue independently. The
visible phase is `snapshotting`. Copying live, changing arrays is never a valid
snapshot. The demo solver uses the same handshake around its small JSON state.

The versioned `KH-CHECKPOINT-1` manifest identifies the run and calculation,
parameters, dimensions, cursor, covered cells, byte layout, and SHA-256 and size
of every file. Publication fsyncs files and directories before indexing them.
Checkpoint images currently contain the complete arrays, including uncommitted
cells that normal DP replay overwrites. They are operational restart data; the
small final DP artifact is a separate output.

A snapshot is counted as replicated after two live workers have acknowledged
its entire verified manifest and all files. Capture resumes after publication;
it does not wait for the second copy. Thus a new local snapshot can be ahead of
remote durability. Busy workers also replicate in a separate thread.

## Ownership and recovery

The leader's default lease is 60 seconds. The agent renews ownership independently
of computation and transfer. Expiration requeues work and records the previous
attempt. Every protected update validates the lease inside the same SQLite write
transaction as the mutation. A late former owner cannot overwrite a replacement's
progress or result. Agent UUIDs also fence a previous incarnation with the same
node name. Each lease has its own mutable working directory:

```text
work/<run-id>/<lease-token>/dp-state/
```

On Linux, solver children receive SIGKILL when their parent agent dies. Intentional
campaign stop follows the existing graceful checkpoint protocol and does not
cause automatic solver restart or a reboot.

The replacement verifies manifest identity, native layout, file sizes, hashes,
and cursor before atomically installing independent mutable copies. Hard links
are not used. CPU count may decrease to fit the replacement's assigned CPUs;
mathematical choices and outputs remain unchanged. A verified restored cache
is acknowledged again after agent re-registration.

Recovery tries newer available checkpoints first, then older ones. If no snapshot
was ever published, execution can start fresh. If snapshot history exists but no
usable snapshot remains, the run fails visibly; it does not silently erase that
history and start over. A rerun or from-scratch submission retains previous runs.

Transfers use 1 MiB buffers and HTTP Range to resume interrupted downloads.
Partial files never become replicas. A poisoned partial prefix triggers a fresh
retry before blaming its source. Confirmed missing or corrupt checkpoint blobs
invalidate the affected worker's claims and schedule repair; timeouts alone do
not prove corruption. Download-lock waits and copying periodically check lease
ownership and cancellation.

## Operator settings and status

```sh
./leader.py serve --database state/leader.sqlite --listen 0.0.0.0:8765 \
  --checkpoint-seconds 1800 --lease-seconds 60 --pin-leader-core
```

`--max-checkpoint-bytes` defaults to 32 GiB per image. Capture and restore also
check available disk space. This is not a global retention quota.

A worker can serve and replicate storage without claiming calculations:

```sh
./agent.py run --leader http://192.168.4.151:8765 --name storage-peer \
  --cpus 0 --storage-only --work-root state/work --storage-root state/blobs \
  --storage-listen 0.0.0.0:8766 --storage-url http://192.168.4.102:8766
```

Status separates these quantities:

| Field | Meaning |
| --- | --- |
| `progress_done` | Current computed cells, including uncommitted work |
| `progress_checkpoint_done` | Latest local committed coverage in this attempt |
| `replicated_checkpoint_done` | Coverage of an available snapshot that reached two live copies |
| `checkpoint_replicas` | Currently live copies of that replicated snapshot |
| `recoverable_checkpoint_done` | Newest available coverage, possibly only one copy |
| `restored_done` | Coverage restored by this attempt |
| `lease_attempt`, `recovery_count` | Attempt history and recovery count |

A snapshot can remain recoverable after losing one of its two copies. Status
shows that degraded copy count. Historical durability timestamps alone do not
assert that two workers are currently reachable.

## Validation and real machines

```sh
make -C king_hamming/cluster check
make -C king_hamming/dp_solver check
king_hamming/cluster/cluster_smoke.py --run
```

The first command runs isolated local tests; it does not SSH to household hosts.
The last command explicitly deploys temporary agents to `.101`, `.102` and `.103`,
uses the local `.151` leader, obtains a replicated partial 13^5 DP checkpoint,
kills only its own original agent, and resumes on another physical worker.
It compares complete value and choice hashes and the artifact with a fresh raw
C calculation. It also waits for the final artifact on both surviving workers.
Evidence is saved under `experiments/`. All executables show help and an example
with no arguments. See [`RECOVERY_EXPERIMENT.md`](RECOVERY_EXPERIMENT.md).

## Remaining limits

Capture pauses computation and copies whole state; snapshots are not incremental.
Conservative retention and collection are implemented; see [`RETENTION.md`](RETENTION.md).
Retention is not a hard total storage quota. Native array checkpoints require compatible machines; they are not portable
mathematical certificates. Admission limits bound one image, not all simultaneous
storage transfers. The solver still uses dense state and complete-array flushing.

This does not install persistent services, implement automatic agent/host restart,
or provide authentication/TLS. Solver stall labels remain diagnostic. Final
artifacts retain the existing three-copy target, which cannot be met with only
two surviving workers. Distributed tile execution and production matching remain
future mathematical and scheduling work.

[`RETENTION.md`](RETENTION.md) documents safe checkpoint retirement, disk limits,
fastest-first scheduling and the previewable `kh.py campaign` generator.
