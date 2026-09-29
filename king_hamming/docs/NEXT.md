# Solver handoff

## Verified stopping point

The local DP now has a persistent, affinity-aware worker pool:

- `--threads N` defaults to one and must fit the inherited Linux CPU set.
- Workers share the file-backed DP arrays and immutable transitions.
- Tiles remain sequential; cells on each `u+v` antidiagonal run in disjoint ranges.
- Barriers publish cell writes before dependent diagonals proceed.
- Physical cores are assigned before SMT siblings; actual placement is logged.
- Checkpoint format is unchanged, and thread count may change on resume.
- Replayed cells reset value and choice, removing partially persisted writes.
- The `cluster/` agent passes DP `threads` from the queue specification.
- `benchmark.py --threads N` records timing and placement.
- Atomic completed-diagonal counters feed an independent 10-second reporter.
- Computed cells and locally checkpointed cells are reported separately.
- The agent polls control without waiting for stdout and drains both child pipes.
- Stops remain latched across quick resume; intentional stops never auto-restart.
- The leader tracks solver heartbeat, work progress, and checkpoint timestamps
  separately and upgrades existing databases without erasing runs.

Run the checks from the repository root:

```sh
make -C king_hamming/solver check
make -C king_hamming/first check
```

Tests compare complete arrays and artifact bytes across worker counts and tile
sizes, resume with different worker counts, poison uncommitted cells, inspect
actual worker affinity, and send SIGTERM during a threaded tile before resuming.
POC comparisons preserve the exact optimum and ordered split.

ThreadSanitizer could not start here (`unexpected memory mapping`). It should be
run on a compatible host when available.

## Measurements

Local single-sample timing and byte comparisons are saved in
`benchmarks/threading_baseline.jsonl`. For 11^5, one/two/four workers took roughly
3.07/1.58/0.88 seconds. These are local smoke benchmarks, not a cluster scheduler
model. Reproduce smaller verified measurements with:

```sh
cd king_hamming/solver
./benchmark.py --case 7:5 --tile-side 4096 --threads 1
./benchmark.py --case 7:5 --tile-side 4096 --threads 4
```

## Exact transition reduction completed

- `kh_estimate --profile-transitions` reports identical-cost groups and removed
  lower/equal gains. Default enumeration cap is 100,000 raw transitions.
- `src/transitions.c` encapsulates construction and same-cost reduction.
- Each group keeps its earliest greatest-gain transition; retained entries are
  restored to original a,b,t order.
- Original one-based IDs are unchanged. Reconstruction decodes them without a
  raw transition array, so existing checkpoints and artifacts remain valid.
- `--raw-transitions` retains the reference mode. Tests compare complete value
  files, choice files and artifacts, and resume in both mode directions.
- `TRANSITIONS.md` contains the proof, memory limits and verification boundaries.
- `benchmark.py` separates construction, tile evaluation and tile-checkpoint
  times. `--verify-with-raw` compares complete state for larger cases.
- `benchmarks/transition_reduction.jsonl` contains repeated local 11^5 samples
  with one/four workers; each sample passes a full alternate-mode comparison.
- Ordinary estimates and `--max-visits` remain conservative raw bounds.

## Recovery milestone completed

Immutable native checkpoint manifests, bounded streaming transfer, two-worker
replication, transactionally fenced lease expiration, and recovery on another
worker are implemented. Local tests cover interrupted and poisoned downloads,
corrupt-newest fallback, a replacement with fewer CPUs, and stale owners.
An isolated real household cluster test also compares complete resumed DP arrays
and output against a fresh raw calculation. See
[`../cluster/RECOVERY.md`](RECOVERY.md) and
[`../cluster/RECOVERY_EXPERIMENT.md`](RECOVERY_EXPERIMENT.md).

## Immutable tile milestone

A bounded `kh_dp_tile` kernel, streaming predecessor assembly and standalone
resumable local/SSH wave driver are implemented. Complete tables match raw DP;
a three-machine 3^5 experiment also passes. See `../cluster/TILES.md`.

## Queued distributed DP completed

The ordinary leader now creates immutable tile jobs with durable dependencies.
Agents fetch predecessor packets directly from worker blob servers. Two live
verified copies are required before dependents advance. A three-machine 13^5
experiment recovered from agent loss and matched every raw value and choice.
All-agent stop/restart also passes local integration tests. See
[`../cluster/QUEUED_TILES.md`](QUEUED_TILES.md).

Queued reconstruction can emit portable KHD1 binary DP artifacts. The independent
verifier and `print_dp.py` accept both binary and JSON. A 5^3 artifact is 56 bytes.

## Shared field foundation completed

`kh_field` and `kh_field_t` construct one immutable four-byte-per-element SUD
table shared by pinned workers. Primitive polynomial generation always uses X
as generator. Complete partitions match the POC across several prime powers and
thread counts. See [`FIELD.md`](FIELD.md).

## Next bounded change

Implement the production matching kernel against the shared field partition,
with checked unsigned indices, bounded memory, and an independently verifiable
compact certificate referencing the separate DP artifact. Preserve encountered
polynomial failures and Hall witnesses when retrying another polynomial.

## Remaining limitations

Production matching and cross-machine matching are not implemented yet.
Automatic agent/host restart and bounded global disk quotas remain open.
Completed intermediate tile artifacts are retained; complete loss of every
replica currently blocks a dependent rather than automatically recomputing it.
The distributed DP restart boundary is a complete tile, not a partial tile.
Whole-state DP checkpoints still flush complete mappings; raw transition
admission remains conservative. Single-machine C DP output remains JSON, with
portable binary conversion available through `print_dp.py`.
