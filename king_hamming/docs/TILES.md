# Immutable distributed DP tiles

The standalone [`tile_driver.py`](../cluster/tile_driver.py) now computes one exact DP
across local worker slots or several SSH machines. Its coordinator keeps a
SQLite index; completed value/choice shards stay on the workers. This is an
algorithmic prototype separate from the long-running leader/agent queue.

## Dependency proof

Every transition consumes `du=a*t` and `dv=b*t`, where all three coordinates
are in 1..p. Therefore `1 <= du,dv <= p^2`. A tile beginning at `(u0,v0)` needs
only the rectangle from `(max(0,u0-p^2),max(0,v0-p^2))` through its upper corner.
Zero-axis values are implicit zeros. Interior bytes are reset before evaluation.

Earlier tiles intersecting this predecessor region form the input cover. Each
has tile-row plus tile-column smaller than the current tile's sum. Thus tiles
with the same sum can execute independently. The driver uses wave barriers;
the C kernel uses the existing pinned thread pool's cell antidiagonals within a
tile. Strict improvement and stable original transition IDs preserve exact ties.

[`tiles.py`](../dp_solver/tiles.py) handles geometry and streams predecessor rows into a
sparse native input file. It never assembles the full matrix in RAM.
`dp_solver/kh_dp_tile` reads one tile-plus-halo image shared by its local threads,
computes tile-only choices and publishes immutable `values.bin`, `choices.bin`
and `tile.json` using fsync and a no-replace directory rename.

## Memory admission

Let tile height/width be H/W and clipped predecessor halo extents be Hu/Hv.
The main data allocation is `8*Hu*Hv + 4*H*W` bytes. Admission additionally
reserves twice the raw transition-array size for sorting, 8 MiB per thread stack,
and 64 MiB of fixed overhead. The default limit is 2 GiB per worker task; Linux
RLIMIT_AS also caps its address space. This limits process address space rather
than the machine-wide filesystem cache. A host runs one tile at a time in this
driver; its threads share that tile's state.

At side 4096, the interior alone is 192 MiB. Halo costs depend on p, so a standard
tile is not automatically admissible for every supported field. Large p can
require a different predecessor scheme; oversized inputs are rejected.

The coordinator streams files using 1 MiB Python buffers. Its transient disk
cache and halo inputs are distinct from its permanent SQLite index. SSH/scp
currently relays peer data through that coordinator, so this is not the final
peer-to-peer transport. The default whole-calculation visit bound is 5 billion
and the default tile-count admission is 10,000.

## Commands

Local workers with distinct pinned CPUs:

```sh
./tile_driver.py 5 3 --work-dir /tmp/dp-tiles --tile-side 7 \
  --workers 3 -o /tmp/dp-tiles/result.json
```

One shared state per SSH machine:

```sh
./tile_driver.py 13 5 --work-dir /tmp/dp-tiles-cluster --tile-side 512 \
  --threads 2 --max-visits 30000000000 \
  --hosts 192.168.4.101 192.168.4.102 192.168.4.103 \
  -o /tmp/dp-tiles-cluster/result.json
```

No arguments print help. Remote deployments use a calculation-specific private
`/tmp/kh-tiled-*` directory. The driver uploads centrally built compatible Linux
binaries and verifies native byte order. It does not install services or reboot
hosts. Remote data is retained for resume, so `/tmp` loss across reboot is a
remaining deployment limitation.

## Commit, recovery and output

Each completed tile is hashed and, when there are at least two worker locations,
copied and fully verified at another location before its index row commits.
Local worker slots provide separate software copies on one machine; they do not
provide independent physical-machine durability. Remote hosts provide the
physical copies. Corrupt/missing primary copies fall back to the second copy.
A tile computation failure retries another worker. Per-worker locks prevent a
retry from overlapping another computation on that worker.

The SQLite index uses WAL and FULL synchronous commits. Resuming with the same
parameters and worker locations skips committed tiles. Changing tile geometry,
kernel content or native byte order is rejected. Changing thread count is safe.
SIGINT/SIGTERM stops after the active tile batch commits, exiting 75. The same
command can resume; `--stop-after-waves N` provides an explicit test boundary.
Previous outputs are never overwritten.

Final reconstruction reads choice shards along the predecessor path and produces
the same ordered run-length `KHDP2-draft` document as the dense C solver. The
standalone independent verifier accepts it. Driver progress currently reports
committed tiles and active waves; it is separate from the queue's run status.

## Validation and current boundary

C tests compare complete values and choices against raw DP for 2^5, 3^3, 5^3
and 7^5, including clipped edges and poisoned interior values. Python tests
assemble complete 5^3 state from independent immutable predecessor tiles.
Driver tests stop/resume, delete a primary shard, retry a failed worker and
compare the reconstructed split with raw C and independent verification.

A real three-host 3^5 calculation used all three machines and matched every raw
value and choice. Its evidence is in
[`report.json`](../cluster/experiments/distributed-tiles-3-5/report.json). Private remote
experiment files were removed after verification; that evidence is a review
record, not a resumable deployment.

The main queue now leases native tile tasks; see [`QUEUED_TILES.md`](QUEUED_TILES.md).
The remaining limitations in this paragraph refer to the standalone SSH driver. SSH transport has no rolling
service deployment, peer-to-peer range transfer, active-task checkpoint finer
than a tile, replica repair daemon, retention, automatic machine restart or
strict global disk quota. The existing leader/agent recovery remains available
for whole-calculation DP and demo jobs. Production field and matching work
remains separate.
