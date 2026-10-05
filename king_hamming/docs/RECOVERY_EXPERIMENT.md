# Household checkpoint recovery experiment

Completed on 2026-09-27, using temporary processes and work directories.

## Setup and result

- Local leader: 192.168.4.151, pinned to CPUs 0 and 8 (one physical core).
- Workers: 192.168.4.101, .102 and .103; two solver threads on the compute worker.
- Calculation: p=13, r=5, tile side 512; 4,826,809 positive-budget DP cells.
- Test-only settings: checkpoint every committed tile, lease 8 seconds. Normal
  defaults remain 30-minute checkpoints and 60-second leases.
- Origin agent on .101 killed only after a partial snapshot reached two copies.
- Replacement: 192.168.4.102, using a new agent incarnation and
  private lease directory. Restored 262,144 committed cells.
- Completed on attempt 2, recovery count 1.
- Result: theta=7529; complete value table, choice table and final
  artifact hashes equal a fresh raw-transition C calculation.
- Final artifact verified on 2 surviving workers. The normal target is
  three, which requires three reachable storage workers.
- Elapsed experiment time: 143.7 seconds, including
  deployment, replication, failure injection, raw comparison and cleanup.

The snapshot selected before failure covered 262,144
cells, while local computation had reached 786,432. This
illustrates why status reports local and replicated progress separately.

## Evidence

[`report.json`](../cluster/experiments/kh-recovery-cc61e009db7d4a1aa3f141142e3527c9/report.json) retains
before/after status, lease history counters, full-state hashes and cleanup results.
[`result.json`](../cluster/experiments/kh-recovery-cc61e009db7d4a1aa3f141142e3527c9/result.json) retains the final DP
artifact; agent and leader logs are beside it. The reference matrices were
hashed completely and then deleted with the private test state.

An earlier successful run is also retained under
`experiments/kh-recovery-d2e5393251ce44a9a8bbb8372f7a8485`. That first harness
removed its local temporary database before shutting down its leader, producing
shutdown-only SQLite errors after successful verification. The harness now keeps
that database until every test agent and the leader have stopped; the second run
has a clean leader log and additionally waits for final artifact replication.

## Repeat

From the repository root:

```sh
make -C king_hamming/dp_solver all
king_hamming/cluster/cluster_smoke.py --run
```

No arguments print help and perform no SSH. The script uses a unique private
`/tmp/kh-recovery-*` root, records owned agent PID/start identities, and refuses
to signal an unrelated process. It leaves failed cleanup visible in its report
and exit status rather than deleting files beneath an unverified process. It
installs no services and performs no machine reboot.

## Verification boundary

This checks whole-calculation recovery across physical machines. Local tests
also cover fewer replacement CPUs, corrupt-newest fallback, interrupted and
poisoned transfers, stop during snapshot capture, stale lease/session rejection,
and storage-only dispatch exclusion. Both coordinator and solver suites pass.

This does not demonstrate simultaneous cross-machine DP tile execution or
failure of the leader's disk. Retention, automatic service restart and the
production matching algorithm remain separate work.
