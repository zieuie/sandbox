# Distributed DP through the ordinary queue

`dp_distributed` now uses the same leader, agents, leases, storage servers and
operator commands as ordinary jobs. The leader stores only the tile DAG and
artifact descriptors. Agents fetch predecessor packets directly from peer
storage; no DP matrix passes through the leader.

```sh
./kh.py --leader http://192.168.4.151:8765 enqueue dp_distributed_5_3.json
./kh.py --leader http://192.168.4.151:8765 status --watch 10
./kh.py --leader http://192.168.4.151:8765 stop --all
./kh.py --leader http://192.168.4.151:8765 resume --all
```

A root waits while its tile DAG executes. A child is queued only after each
predecessor has two live complete artifact copies. Independent ready tiles may
run on different machines. Each agent owns at most one tile computation, with
its pinned threads sharing the admitted tile-plus-halo state. Parent progress
counts cells in two-copy committed tiles. After all tiles are ready, an ordinary
leased reconstruction job fetches only choice packets along the optimal path.

Tile packets are streamed gzip tar files containing values, choices and native
layout metadata. Extraction checks identity, exact member names, regular-file
types, sizes and byte order. Transfers hash complete content, resume partial
HTTP downloads and periodically validate the requesting lease. Transfer liveness
reports byte progress; native computation reports cell progress. Completed
native scratch arrays are removed after the agent's durable completion is
acknowledged; compressed artifacts and run history remain.

Expiration, new agent incarnations and stop latching use the existing fenced
protocol. Uncommitted tiles are replayed under fresh private leases. Ordinary
engine failures get one retry, preferring another worker for 30 seconds; repeated
engine failure fails visibly. Missing/corrupt primary replicas fall back to other
live sources. Restarted agents revalidate their retained CAS files before
reclaiming replica ownership. This handles simultaneous agent restart without
silently trusting a missing disk.

The root index and child history remain in SQLite across leader restart. Normal
status shows parent calculations and active children, with final-result replica
counts based on currently live nodes. Native tile artifacts remain in the worker
artifact index for inspection; they do not clutter the main result table.

## Compact final output

Set `arguments.artifact_format` to `"KHD1"` for a portable binary DP split.
This reuses the POC's canonical varint/run-length encoding and SHA-256 checksum.
The production parser supports the full agreed uint32 field range. No native
matrix or field generator value is included. The 5^3 split is 56 bytes.

```sh
../solver/print_dp.py split.khdp --verify
../solver/print_dp.py split.json --binary-out split.khdp
../solver/verify_dp.py split.khdp
```

JSON remains the default for compatibility with earlier examples. The POC
matching program can consume KHD1 within its original small-case bounds. Binary
structure, budgets and gain can be checked cheaply; proving the DP optimum still
requires the independent recurrence or complete reference-table comparison.

## Evidence

Local tests run real leased C tiles, reconstruct the exact split, verify binary
output, and restart all agents after partial replication. They preserve the
same parent and committed frontier and compare with raw C.

[`report.json`](../cluster/experiments/kh-recovery-queued-a2fa607f6d254f2b8136085be755d843/report.json)
records a three-machine 13^5 run. After 262,144 replicated cells, its .101 agent
was killed. The active child expired and completed on .102 under attempt two.
All 25 tile value/choice arrays and the ordered split match raw C (theta 7529).
A late completion from the old child lease was rejected. Elapsed time, including
raw verification and cleanup, was 54.7 seconds. All private experiment processes
and directories were removed.

The standalone SSH driver remains useful for manual experiments; its transport
and index are separate from this ordinary queue integration. See `TILES.md` for
the dependency proof and memory formula.

## Current limits

At least two live storage workers are needed to advance the DAG. A tile has no
checkpoint finer than its immutable completion boundary. Native packets require
compatible hosts. Intermediate tile artifacts currently remain retained; their
retention policy and recovery after losing every copy need further work. Final
DP remains a separate stage from field selection and bipartite matching. No
production services or automatic host reboots are installed by these utilities.
