# Ownership-partitioned matching

An isolated C proof of concept for [the partitioned design](../docs/PARTITIONED_MATCHING.md).
It leaves the existing solver intact. The managed adapter integrates with the
capacity-first campaign; the original standalone runner remains an experiment tool.
Python launches processes and packages their results; field construction and matching
are native C.

## Run

For managed scheduling and automatic fallback, see
[Capacity campaign](../docs/CAPACITY_CAMPAIGN.md). `match_partitioned` reserves a
fixed owner group, enforces native memory ceilings, fences remote peers by lease,
and publishes all-owner KMP1 snapshots only after durable cluster acknowledgement.
Pause and worker-loss recovery restore the last complete phase, rebuild field
state and validate saved edges. Changing the owner count during restore is not
yet supported. Final results remain standard independently verified KHM1.

The commands below use the **unmanaged** benchmark runner:

From the repository root:

```sh
make -C matching_solver_multi
python3 matching_solver_multi/tests/check.py
python3 matching_solver_multi/run.py examples/7_5.khdp \
  --hosts local,local --threads 1 --verify \
  --output-dir /tmp/kh-multi-example
```

Output directories must be new. Local owners use disjoint CPUs from the launcher's
affinity. To use two **reserved, idle** machines, replace `local,local` with their
comma-separated IPv4 addresses. Passwordless SSH/SCP and direct TCP connectivity
are required. Remote CPUs default to `0..threads-1`; `--remote-cpus` overrides that
set. Each compute thread pins itself to one allowed CPU. `--timeout` bounds each
native process; `--batch` bounds generated message batches (default 65,536 records).
This runner does **not** reserve machines or coordinate with the campaign.

For the authorized two-host experiment, `borrow_workers.py` records original
agent commands and temporarily switches `.107`/`.108` to storage-only service.
It first pauses dispatch and waits for active work to quiesce; other agents then
resume computing. Always restore the recorded hosts, including after a benchmark
failure (wait for experiment processes to exit first):

```sh
python3 matching_solver_multi/borrow_workers.py restore \
  --state cluster/deployments/continuous-campaign \
  --record matching_solver_multi/experiments/borrowed-workers.json
```

This is an experiment-specific operations helper, not a reusable resource lease
or a general scheduler interface. Its record is an operational recovery artifact.

The output includes a standard `result.khmatch`, per-owner logs and assignments,
and `metrics.json`. `--verify` runs the existing independent certificate verifier.
Verification is intentionally outside reported solve time. Remote scratch directories
are unique, reported in the metrics, and retained for diagnosis.

## What is implemented

- Cells and their left requests are cyclically owned, avoiding the severe request
  imbalance of contiguous cell ranges. Right vertices have contiguous ownership.
- Field generation is divided among owners and threads, routing each label to its
  cell owner. No owner retains the entire field-label array.
- A frozen-matching, multi-source BFS forest accepts one predecessor per right.
  One free endpoint per root is selected, yielding vertex-disjoint augmenting paths.
  Augmentation completes before the next search barrier.
- An exact, replicated `q`-bit bitmap per sender suppresses repeated discovery of
  a right during each BFS. This bounds discovery records by `owners * q` per BFS,
  not by the number of implicit graph edges.
- Exhausting reachability exports the visited-left Hall witness. A full matching
  exports the same packed-choice KHM1 certificate as the existing solver.
- Owned outputs are merged in canonical order with a streaming heap, rather than
  rebuilding a global mate array in the launcher.

This is an augmenting-forest algorithm, **not** a Hopcroft–Karp blocking-phase
implementation. Do not assume its phase count or scan complexity matches that
algorithm. The test suite compares small cardinalities with a separate augmenting
oracle, checks ownership coverage, verifies certificates, and rejects corrupted
certificates. Its passing cases do not establish coverage of every failure path.

## Memory and measurements

For owner `i`, the principal allocated state is approximately

```
28 * left_requests_i + 16 * right_vertices_i
  + 4 * field_labels_i + 4 * cells_i + ceil(q / 64) * 8 bytes.
```

This is what `owned_bytes` measures. It excludes block metadata, message vectors,
batch buffers, stacks, allocator overhead and executable/runtime pages. Peak RSS
is measured separately. This straightforward representation is larger than the
lean target layout in the design document; it still partitions the large arrays.

To build instrumented copies of the unchanged replicated solver and compare:

```sh
make -C matching_solver_multi baseline
python3 matching_solver_multi/benchmark.py examples/7_5.khdp \
  --hosts 192.168.4.107,192.168.4.108 --threads 1 --repeats 2 \
  --output-dir /tmp/kh-multi-comparison
```

The benchmark alternates engine order and verifies every certificate. Baseline
executables live under `benchbin/`; existing executables are never overwritten.
The baseline transport counter wraps native `read`/`write`; prototype counters
measure framed peer payloads. These are application bytes, **not** Ethernet bytes:
SSH/TCP overhead, staging, and certificate downloads are excluded. Solve wall time
includes startup and certificate creation/download, but excludes staging and
independent verification. Summed component peak RSS is not a simultaneous cluster
memory reading; Python launcher/verifier and SSH process memory are excluded.
Small-process RSS can also include inherited pre-exec high-water marks.

## Deliberate limitations / next experiments

- No checkpoints, recovery, admission control, campaign adapter, or production
  lease fencing. Failures abort the experiment; in-place augmentation is not a
  recoverable distributed transaction. Do not use this for valuable long runs.
- Trusted LAN only. The session token is not transport encryption/authentication.
  Wire and owned-output formats currently require little-endian machines.
- All owners synchronize at batch/level boundaries. I/O threads are created for
  each exchange; batching, persistent I/O, and overlap deserve measurement next.
- The atomic sent bitmap and barriers can limit multicore scaling. More threads
  do not imply all cores remain busy during network waits.
- Limits are experimental: at most 16 owners, 64 compute threads per owner, and
  32-bit graph identifiers. There is no automatic safe RAM budget. Start small.
- Killing an SSH launcher is not remote cancellation. Remote processes have a
  finite timeout; allow that deadline to expire before reusing machines after a
  failed run. Successful runs exit normally.
- The certificate verifier currently reconstructs field state, independently of
  the partitioned solver; its RAM is outside these native measurements.

Keep the existing campaign solver until measured memory savings justify the
runtime/network cost on fields that cannot otherwise fit. See `EXPERIMENTS.md`
for actual measurements and the resulting recommendation.
