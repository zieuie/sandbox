# Checkpoint retention and workload scheduling

## Retirement safety

The leader keeps at least three distinct-cursor, currently two-copy checkpoints
per run by default. `--checkpoint-keep N` changes this minimum; N must be at
least two. A pending newer image does not count as a replicated replacement.
Older images behind those successors may be retired. An active restore pins its
selected image until acknowledgment, another selection, or lease expiration.

Retirement preserves the manifest, durability timestamp, run and lease history.
It removes the obsolete replica claims and queues those holders' bulk objects
for collection. Retired images cannot be selected or acknowledged by a stale
transfer. A still-valid calculation may replay and republish an older image.

Worker collection runs once a minute and handles at most 128 objects per batch.
A shared hash referenced by any retained checkpoint or final artifact remains
protected. Workers serialize collection, capture/publication, restore and
replication with a storage transaction lock. This prevents deleting a just-written
object before its publication reaches the leader. Download and restore pins
protect the separate remote-read boundary. Lost deletion acknowledgments are
idempotent: a missing obsolete file can be acknowledged again.

A failed snapshot disk admission triggers one collection attempt before retry.
The existing byte limit and free-space checks remain in force. Publication never
deletes a newer result or a mutable lease directory to make room.

This is conservative retention, not a hard disk quota. Several newer pending
images can remain ahead of the retained replicated frontier. A prolonged
replication outage prevents safe retirement and may exhaust storage, producing a
visible error. Unindexed crash leftovers, retired working directories, partial
downloads and final artifacts are not automatically deleted. The leader index
and historical manifests continue to grow.

## Runtime ordering

Ordinary equal-priority jobs dispatch in increasing estimated serial runtime,
then creation time. Manual `enqueue --priority N` still overrides ordinary order.
Duplicates reuse existing runs; `--rerun` and `--from-scratch` create retained
separate runs. Existing database estimates migrate without discarding attempts.

For DP, the model is the conservative raw visit bound `B^2 * p^3`, divided by
`--visits-per-second` (default 100 million). This is a coarse ordering model, not
a measured wall-clock promise. It excludes replication and checkpoint overhead,
transition reduction and machine/thread differences. Demo estimates use steps
and delay. Unsupported future stages sort behind known work.

Preview locally without contacting the leader:

```sh
./kh.py campaign --limit 20 --max-visits 5000000000
```

Submit the same admitted frontier:

```sh
./kh.py --leader http://192.168.4.151:8765 campaign --limit 20 --submit
```

The generator enumerates prime p and nontrivial odd r with p^r <= UINT32_MAX,
checks dense state and conservative visit limits, and sorts by work. Repeated
submission is safe through duplicate reuse. Increase the limit or admission
bounds to expand the frontier. Under the agreed 32-bit field limit, the supported
prime-power table is finite. It currently creates DP-stage entries only.

## Evidence

Five retention tests cover live replica requirements, active/expired restore
pins, shared hashes across runs and artifacts, concurrent publication, and lost
cleanup acknowledgment. Four scheduling tests compare the Python model with the
C estimator, verify admitted field ordering and exercise actual queue dispatch,
manual priorities, duplicate reuse and retained reruns.

The household test in
[`report.json`](../cluster/experiments/kh-recovery-fdd2000c1a23442fa09d1c950f452d27/report.json)
retired 19 of 25 snapshots, retained six including the pending frontier, recovered
an agent killed on .101 on another physical worker, and matched both full DP
arrays and the final artifact with raw C (theta 7529). Both surviving workers
held the final artifact. Owned processes and private directories were cleaned up.
