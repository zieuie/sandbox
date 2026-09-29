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
local snapshots. The cluster adapter saves each committed phase through the generic checkpoint
handshake. The worker waits until the checkpoint has been indexed and replicated
before continuing. A single phase longer than the checkpoint interval finishes
before its next snapshot can be taken.

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

Add `--distributed --workers N` to reserve 2-8 agents for one matching.
`--threads C` gives each node's single native shard process C assigned CPUs, so
`--workers 8 --threads 2` uses eight compact field copies and sixteen cores.
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
phase through the generic checkpoint store, and fences the group if any partner
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
the cluster. Larger-field resident-memory validation and intra-phase checkpoints
remain necessary before another giant field is submitted.

Each native solver, coordinator, and distributed shard reads its own process
usage at shutdown. Cluster runs store approximate user-plus-system CPU time and
Linux peak resident memory in the leader's `resource_usage` table, keyed by
lease attempt and shard. This deliberately excludes Python orchestration and
does not claim simultaneous whole-machine memory precision. The normal cluster
status command prints the retained values as CPU seconds and peak-RSS MiB.

## Retained household matching campaign

A separate matching campaign is retained at `192.168.4.151:8051`; consult
[`NEXT_CONVERSATION.md`](../NEXT_CONVERSATION.md) and the live status command
before assuming its state. It does not replace the ongoing DP agents. When active, its
eight matching agents use CPUs 0 and 1 at lower process priority. Its persistent
leader database, launch identities, and locally archived certificates are under
[`cluster/deployments/match-overnight/`](../cluster/deployments/match-overnight/).
Of the 44 admitted saved DP fields, 41 have complete certificates; 2^25, 7^9,
and 5^11 retain unfinished queue records. Admission checks the native kernel's
conservative memory estimate against a 2 GiB per-job cap, as well as a 50 million
element and 160 billion implicit-edge cap.

From the repository root:

```sh
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8051 status --watch 60
python3 king_hamming/matching_solver/launch_overnight.py extend --max-q 50000000 --max-edges 160000000000
python3 king_hamming/matching_solver/launch_overnight.py repair
cat king_hamming/cluster/deployments/match-overnight/STATUS.md
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8051 stop --all
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8051 resume --all
```

The status command now shows the field and polynomial on each active node.
`STATUS.md` links centrally downloaded KHM1 files and lists every verified
polynomial obstruction encountered. The detached `witch_hunt.py` watcher
checks the queue every two minutes, archives new certificates by SHA-256,
tries the next primitive polynomial only after a verified Hall obstruction,
reattaches dead campaign-owned matching processes, and collects newly
finished DP artifacts every 30 minutes. It then enqueues
new DP fields that fit the current admission limits. Its output is in
`match-overnight/watcher.log`; `manifest.json` records its process identity.
The `repair` command is idempotent for healthy processes and can be used
manually after an interruption. It does not kill a live but unresponsive
process or reboot a host. A newly launched campaign can be created with
`launch_overnight.py start` only when `match-overnight/` does not already exist.

Long native matching phases on the currently deployed workers may show
`heartbeat-missing` during a long native phase in the current deployed bundle;
its C kernel reports at phase boundaries. A subsequent code bundle emits a
heartbeat when the native process is consuming CPU. Check the agent and
kernel process before treating that status as a stalled calculation. The watcher checks CPU activity in newer code bundles, but the active matching
workers still run the older bundle. Individual native phases cannot be checkpointed
until they complete. Fields above the current 2 GiB admission cap need a
sharded matching path before the campaign can cover the full uint32 range.
The exact remaining fields and readiness work are in [FULL_SCALE.md](FULL_SCALE.md).
