# Checkpoint recovery handoff

> **Historical (2026-09-28).** Handoff notes from the recovery milestone; the protocol itself is in [RECOVERY.md](RECOVERY.md).

The checkpoint replication and cross-worker recovery milestone is complete.
The POC and exact DP recurrence/tie choices remain unchanged. The user authorized
isolated experiments on the household machines; no services or reboots were
introduced.

## Completed and tested

- Consistent immutable snapshots while the solver waits at a committed boundary.
- Independent heartbeats/control during capture, lease renewal and transfers.
- Versioned manifests, bounded-memory resumable downloads and atomic restore.
- Two-copy checkpoint replication on busy workers, separate local/remote status.
- Expiring leases, transactionally fenced updates, per-agent session identities,
  per-lease working directories, and parent-death termination of solver children.
- Recovery on another worker with fewer CPUs; older-snapshot fallback if the
  newest one is damaged. Previous manifests, results and attempts are retained.
- Confirmed bad replica invalidation/repair, poisoned-prefix retry, and
  cancellable waits for concurrent downloads.
- Storage-only agents and an explicit isolated SSH experiment utility.

## Evidence and commands

```sh
make -C king_hamming/first check
make -C king_hamming/solver check
king_hamming/cluster/cluster_smoke.py --run
```

Final checks pass: 10 recovery tests, 6 supervision tests, existing three-worker
integration, and C mathematical solver checks. Two real-host experiments pass
using .101/.102/.103 and the local .151 leader. A killed origin's 13^5 DP resumes
on another physical worker and matches every byte of the raw reference tables
and final artifact (theta 7529). The final experiment also verifies the artifact
on both surviving workers. Temporary deployments are cleaned up.

[`RECOVERY.md`](RECOVERY.md) explains modules, protocol, status and limits.
[`RECOVERY_EXPERIMENT.md`](RECOVERY_EXPERIMENT.md) links actual evidence and
records the first harness's fixed shutdown-order issue.
`../inventory/RECOVERY_PREFLIGHT.json` records all nine reachable compatible hosts.

## Further progress after the recovery milestone

Conservative checkpoint retention and shared-blob collection are now implemented.
Five new retention tests and four scheduling tests pass. The subsequent household
experiment retired 19 of 25 images while preserving exact recovery and two final
artifact copies; see `RETENTION.md`. Queue dispatch now sorts equal-priority jobs
by estimated runtime; `kh.py campaign` previews or submits an admitted table.

The current next algorithmic milestone is a bounded immutable DP tile kernel and
cross-machine dependency scheduling. Current whole-calculation recovery remains
a correctness reference while that work proceeds.

## Remaining limits

Snapshots pause computation and copy complete state. Native checkpoint arrays
require compatible hosts. The final DP artifact remains KHDP2-draft JSON. No hard total storage quota, authentication/TLS, production service deployment, automatic
agent/host restart or production matching solver is implemented. Solver stall
warnings remain diagnostic. Normal checkpoint interval is 1800 seconds and
lease duration 60 seconds; the real-host test intentionally accelerates both.

## Continued implementation status

Ordinary queued distributed DP is now implemented: `distributed.py` owns the
leader DAG; `distributed_solver.py` streams peer tile packets and reconstructs
final output. The real 13^5 queued household test survives loss of the .101 agent,
rejects the stale lease, and matches every raw value and choice. See
`QUEUED_TILES.md`. Tests also restart all three agents mid-calculation and
rebuild their retained replica indexes.

`solver/artifacts.py` reads/writes compatible KHD1 binary splits; `print_dp.py`
prints/converts/verifies them. The queued 5^3 binary is 56 bytes and passes both
production and POC independent verification.

Production field work has begun: `solver/src/field.c` and `kh_field` build a
single shared SUD table with two pinned parallel passes and primitive-X order
testing. Full buckets match independent POC polynomial arithmetic for 2^3,
2^5, 3^3, 5^3 and 7^5 with one/two threads. 13^5 builds successfully with four
threads and polynomial [2,4,0,0,0,1]. The production matching kernel and its
portable certificate remain the active next work. Do not claim distributed
matching, automatic host recovery or complete intermediate-tile retention.
