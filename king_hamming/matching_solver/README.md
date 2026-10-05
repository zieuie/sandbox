# Matching solver

The local solver consumes a saved KHD1 DP split, builds one primitive-X field,
finds an exact maximum matching in C, and writes a compact KHM1 certificate.
It accepts any valid matching. Each completed artifact is independently checked
before publication. The default matching attempt runs on one worker with parallel BFS
and parallel augmenting-path proposals. A native multi-node engine is also available
for bounded distributed fields. Python reserves nodes, authenticates streams, and
launches processes; C owns field construction, neighbor scans, BFS layers,
augmenting paths, matching state, checkpoints, and certificate bytes. The cluster can queue independent pinned
field attempts, replicate phase checkpoints, resume on another worker, and
replicate final certificates. See [DESIGN.md](DESIGN.md) for the solver and cluster contracts.

From the repository root:

```sh
make -C king_hamming/matching_solver check
python3 king_hamming/matching_solver/match.py king_hamming/examples/5_3.khdp -o /tmp/match_5_3.khmatch --threads 4
python3 king_hamming/matching_solver/verify_match.py /tmp/match_5_3.khmatch --dp king_hamming/examples/5_3.khdp --hydrate /tmp/match_5_3.tsv
```

`match.py` generates a primitive polynomial when `--poly` is omitted. Use
`--poly 2,3,0,1` to pin the example field, or `--start N` to resume automatic
candidate selection. An exact Hall obstruction from an automatic attempt is
retained as `OUTPUT.poly-N.hall.khmatch`; the solver then tries the next primitive
polynomial, until success or candidate exhaustion. Set `--max-attempts N` to
cap the number of attempts and resume later with the reported `--start`. If
`--poly` is pinned, its obstruction is written to `OUTPUT` and the command exits 2. Operational errors
exit 1. Outputs are never overwritten.

`--threads N` uses N pinned workers for field construction, breadth-first
matching layers, and augmenting-path proposals. Workers claim disjoint paths
against an unchanged matching; the main thread checks and commits those paths
after they finish. An exact serial search resolves a phase only if claim
conflicts prevent parallel progress. All threads share one field and matching
state, with no edge matrix. `--max-bytes` sets native address-space and
verifier memory limits, with a 2 GiB default; native admission includes claim
scratch and worker stacks. The kernel prints completed phase cardinality,
parallel augmentations, and edge scans to stderr. The standalone `kh_match_kernel` uses an internal compact
request-block text file and returns a bit-packed payload; use `match.py` for
normal DP artifacts and complete KHM1 certificates. Every executable and script
prints help with an example when run without arguments.

A complete four-worker 13^5 run is saved with its DP input in
[examples/](examples/). The certificate covers 371,293 requests in 371,376
bytes and can be verified without the household campaign database.

A pinned field attempt can save a phase-boundary checkpoint and resume it
later, even with a different worker count. The normal interval is 1,800 seconds;
`--checkpoint-seconds 0` saves every completed phase. For a short pause/resume
demonstration from the repository root:

```sh
python3 king_hamming/matching_solver/match.py king_hamming/examples/5_3.khdp -o /tmp/pending.khmatch --poly 2,3,0,1 --threads 4 --checkpoint /tmp/match_5_3.khcp --checkpoint-seconds 0 --stop-after-phases 1
python3 king_hamming/matching_solver/match.py king_hamming/examples/5_3.khdp -o /tmp/resumed.khmatch --poly 2,3,0,1 --threads 2 --resume /tmp/match_5_3.khcp
```

The first command intentionally exits 3 after saving a complete phase; it
does not create `pending.khmatch`. The second verifies the checkpoint's DP
hash, polynomial, graph layout, checksum, and every selected edge before
continuing. An ordinary run omits `--stop-after-phases`. The local checkpoint
path is replaced atomically at later phases; use separate paths to keep old
local snapshots. The distributed cluster adapter also creates bounded commit points inside the
initial augmentation phase. It checks the configured interval at each root batch,
then waits until each selected KHS1 image has been indexed before continuing.
Recovery imports the canonical partial matching and recomputes transient BFS state.

The certificate records one neighbor index per left request, the referenced DP
hash, primitive polynomial, and checksum. The verifier reconstructs field
labels and selected edges, checks uniqueness, and validates any Hall witness.
`--hydrate` writes a tab-separated list of individual matching assignments;
it does not expand the full construction grid. DP optimality is checked
separately by `king_hamming/dp_solver/verify_dp.py`.

To enqueue one pinned field attempt on a leader running the current code bundle:

```sh
python3 king_hamming/matching_solver/submit.py king_hamming/matching_solver/examples/13_5.khdp --poly 2,4,0,0,0,1 --threads 4 --leader http://127.0.0.1:8041 --enqueue
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8041 status
```

Add `--distributed --workers N` to reserve 2-9 agents for one matching.
`--threads C` is a per-node ceiling. Each native shard uses the smaller of C and
that agent's registered CPU allocation, allowing heterogeneous groups such as a
12-core Merlin shard plus smaller household workers.
For example:

```sh
python3 king_hamming/matching_solver/submit.py \
    king_hamming/examples/5_3.khdp --poly 2,3,0,1 \
    --distributed --workers 4 --threads 2 \
    --leader http://127.0.0.1:8041 --enqueue
```

Omit `--enqueue` to print a JSON specification for review; add `--rerun` to
retain another attempt of the same pinned field. The saved KHD1 remains a
separate result; for now its exact bytes (up to 700 KiB) are embedded in the
small queue request so a new worker can stage it without a separate artifact
input protocol. The final KHM1 references the DP SHA-256 rather than embedding
its bytes. An exact Hall obstruction is also a verified KHM1 result and stays
in the results index; choose another primitive polynomial in a new attempt.
The retained campaign watcher automatically retries the next primitive polynomial after an exact verified obstruction.

`make check` includes an isolated four-agent/eight-core cluster test that checks
checkpoint replication, restoration into a fresh directory, native resume,
and the downloaded KHM1 certificate. This does not modify the running DP
campaign. Deploy a fresh code bundle before submitting matching jobs to an
older leader or worker deployment.

The [distributed matching engine](DISTRIBUTED.md) runs as a
`match_distributed` cluster job. The leader atomically reserves the requested
agent group, runs one native shard per node across its allocated cores, and gives
their binary streams to a native coordinator. It replicates every committed KHS1
barrier through the generic checkpoint store, and fences the group if any partner
agent restarts or loses its heartbeat.
An isolated cluster test kills and restarts a reserved partner, then verifies
recovery and the final KHM1 certificate. A private two-host test on `.107` and
`.108` completed 13^5 in 10.74 seconds with two threads per node, including a
deliberate partner failure; a fresh lease restored 368,414 assignments from two
checkpoint replicas and the final KHM1 independently verified. Neither test
changed the running DP campaign. The old Python algorithm remains only as a
regression/reference implementation. The production path still has bounded field
and edge admission limits. Each shard now
consumes edge labels locally, keeps replicated committed matching/BFS state, and
returns only discovered vertices or complete path proposals. The coordinator
merges proposals and broadcasts assignment deltas; edge labels no longer cross
the cluster. Larger-field resident-memory validation remains necessary before another giant field is submitted.

Each native solver, coordinator, and distributed shard reads its own process
usage at shutdown. Cluster runs store approximate user-plus-system CPU time and
Linux peak resident memory in the leader's `resource_usage` table, keyed by
lease attempt and shard. This deliberately excludes Python orchestration and
does not claim simultaneous whole-machine memory precision. The normal cluster
status command prints the retained values as CPU seconds and peak-RSS MiB.

## Matching in the campaign

The separate overnight matching campaign (port 8051, `launch_overnight.py`, the `witch_hunt.py`
watcher) was retired, and its deployment `cluster/deployments/match-overnight/` was removed on
2026-10-02. Matching now runs inside the continuous campaign
([../docs/CONTINUOUS_CAMPAIGN.md](../docs/CONTINUOUS_CAMPAIGN.md)): fields that fit a GPU use
`match_gpu`, larger ones `match_gpu_blocks` ([../docs/GPU.md](../docs/GPU.md)), and this CPU
matcher remains the independent engine and a fallback. `launch_overnight.py` and
`witch_hunt.py` are kept for reference; the results are in [../results.md](../results.md).

## Analyze and independently render certificates

`analyze_choices.py` measures a certificate's choice/delta entropy and ordinary
compression ratio. It is diagnostic, not verification:

```sh
python3 king_hamming/matching_solver/analyze_choices.py result.khmatch --dp result.khdp
```

For the stronger sanity check, `row_verifier/kh_verify_rows` independently
rebuilds the field and P/Q sets, renders the actual P&E permutations, and
exhaustively checks their pairwise distances within explicit work limits.
