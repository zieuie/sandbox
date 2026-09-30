# Native multi-node matching engine

`native_coordinator.py` runs one exact matching across 2-256 native shard
processes (2-9 when scheduled through the generic cluster). Python performs only
cluster work: it opens local, SSH, or lease-authorized agent streams and passes
their file descriptors to `kh_match_distributed`. It never decodes a frontier,
edge, matching assignment, checkpoint, or certificate payload.

`kh_match_distributed` parses and validates KHD1, owns the canonical matching,
merges BFS discoveries and nonconflicting path proposals, extracts an exact Hall
closure, writes KHS1, and emits KHM1. Worker i is a
`kh_match_worker` process responsible for left requests congruent to i modulo the
worker count. Each worker builds one complete primitive-X field, keeps a replica
of the committed pair arrays and BFS distances, scans only its owned frontier,
and proposes complete augmenting paths in C. Coordinator and shards communicate
with fixed binary KHW1/KHR1 frames. The final KHM1 is independently checked by
the adapter before the cluster publishes it.

At each BFS barrier, the coordinator broadcasts only the current left frontier.
Each shard scans its congruence-owned vertices, consumes neighbor labels locally,
and returns only newly reached matched-left vertices plus a free-right flag.
After the shortest layer is known, shards rescan locally and return compact path
proposals. The coordinator accepts vertex-disjoint proposals and broadcasts only
the committed assignment delta. No edge-label frame crosses a process or machine.
For a Hall obstruction, shards return one q-bit reachability bitmap. `--max-edges`
bounds BFS scans within each phase and `--max-field-elements` bounds each field.

The tradeoff is replicated committed pair and distance state on every shard.
This is appropriate for 2^25 under the current 2 GiB limit, but larger fields
will eventually need truly partitioned state rather than replication.

On orderly exit, every shard returns its lifetime CPU microseconds and Linux
peak RSS bytes in the final native wire response. The C coordinator reports
those records, plus its own process usage, through the authenticated cluster
lease. SQLite retains every lease attempt rather than collapsing recovery runs.

During the potentially long initial augmentation, the coordinator commits bounded
root ranges and may atomically write KHS1 before the full augmentation phase ends.
It checks the configured checkpoint interval at each bounded range and always at
a complete augmentation barrier. A restored group starts from that canonical
matching and recomputes transient BFS state, so shard-count changes remain safe.
A KHS1 image contains the exact DP SHA-256, dimensions, primitive
polynomial, commit sequence, cardinality, compact assignments, and SHA-256. A fresh group
of any supported size can import it and validate every selected edge against its
rebuilt fields. A failure loses at most the work since the latest bounded commit and
cannot publish a partial certificate.

From the repository root, a local four-process check is:

```sh
make -C king_hamming/matching_solver check
python3 king_hamming/matching_solver/native_coordinator.py \
    king_hamming/examples/5_3.khdp --poly 2,3,0,1 \
    --worker local --worker local --worker local --worker local \
    -o /tmp/four-worker.khmatch
python3 king_hamming/matching_solver/verify_match.py \
    /tmp/four-worker.khmatch --dp king_hamming/examples/5_3.khdp
```

To pause deliberately after phase 1 and resume with a different worker count:

```sh
python3 king_hamming/matching_solver/native_coordinator.py \
    king_hamming/examples/5_3.khdp --poly 2,3,0,1 \
    --worker local --worker local -o /tmp/pending.khmatch \
    --checkpoint-dir /tmp/match-phases --stop-after-phases 1
python3 king_hamming/matching_solver/native_coordinator.py \
    king_hamming/examples/5_3.khdp --poly 2,3,0,1 \
    --worker local --worker local --worker local -o /tmp/resumed.khmatch \
    --checkpoint-dir /tmp/resumed-phases \
    --resume /tmp/match-phases/phase-00000000000000000001.khstate
```

The first command exits 3 and leaves no `pending.khmatch`. Wrong-input,
damaged, or mathematically invalid KHS1 files are rejected before search resumes.

The generic cluster runs this path as `match_distributed`. `--threads` is a per-shard
ceiling: every reserved node contributes the smaller of that ceiling and its
registered CPU allocation, so a 12-core coordinator can work alongside smaller
workers without underusing the larger host. One agent coordinates
and holds the solver lease; the leader atomically reserves the requested 2-9 node
group. Partner agents expose separately authorized opaque streams and launch only
the trusted C shard command. Pairwise SSH credentials are not required. Each
KHS1 commit enters the generic content-addressed checkpoint store before the next
phase starts. If a partner restarts or its heartbeat expires, the leader fences
the group and a fresh group restores the latest replicated phase.

```sh
python3 king_hamming/matching_solver/submit.py \
    king_hamming/examples/5_3.khdp --poly 2,3,0,1 \
    --distributed --workers 4 --threads 2 \
    --leader http://127.0.0.1:8041 --enqueue
```

The isolated four-agent/eight-core regression kills and restarts a reserved
partner, verifies stale-lease rejection, restores a canonical checkpoint with a
new group, and independently verifies the final KHM1. A private two-host test on
`.107` and `.108` completed 13^5 in 10.74 seconds with two threads per node,
including a deliberate partner failure. A fresh lease restored 368,414 matched
assignments from two checkpoint replicas, and the final KHM1 independently
verified. The test did not touch the DP campaign.

The earlier Python distributed matcher has been removed. Differential,
repartitioning, and partial-commit coverage now exercises the native engine.
Deploy a fresh cluster bundle before submitting distributed matching to a
retained campaign.
