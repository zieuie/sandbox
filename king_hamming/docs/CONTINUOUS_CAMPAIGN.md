# Continuous household campaign

The unified campaign owns DP and matching on one leader and a ten-agent pool:
workers `.101` through `.108`, Merlin (`.151`) with its leader physical core
reserved, and Gawain (`.156`). Pellinore (`.152`) was retired on 2026-10-04. Every
machine has an NVIDIA GPU and is on both Wi-Fi and the wired switch (below).
Its retained state is
`cluster/deployments/continuous-campaign` and its leader is port `8061`.

## Check status

```sh
# Prime-by-exponent table across every retained campaign. Cells are the exact
# permutation counts (rows); ^ means matching, * means obstruction.
python3 king_hamming/campaigns/result_table.py

# Concise DP-to-matching table and feeder backpressure state.
python3 king_hamming/campaigns/king_hamming.py \
  --state king_hamming/cluster/deployments/continuous-campaign status

# Nodes, CPU allocations, active leases, progress, checkpoints and artifacts.
python3 king_hamming/cluster/kh.py \
  --leader http://192.168.4.151:8061 status

# Include completed history, artifacts, checkpoints, and per-process resources.
python3 king_hamming/cluster/kh.py \
  --leader http://192.168.4.151:8061 status --verbose

# Refresh the live cluster view every 30 seconds.
python3 king_hamming/cluster/kh.py \
  --leader http://192.168.4.151:8061 status --watch 30

# Capture a repeatable before/after utilization and throughput sample.
python3 king_hamming/cluster/benchmark_canary.py \
  --leader http://192.168.4.151:8061 --seconds 120 -o canary.json

# Feeder and leader logs.
tail -f king_hamming/cluster/deployments/continuous-campaign/feeder.log
tail -f king_hamming/cluster/deployments/continuous-campaign/leader.log

# On a worker host, show every native thread and its last CPU.
ps -L -C kh_match_worker -o pid,tid,psr,pcpu,comm
ps -L -C kh_dp_tile -o pid,tid,psr,pcpu,comm

# Verify each compute thread has a one-CPU mask (the process main thread may
# retain the agent allocation). Replace PID with the kh_match_worker PID.
for task in /proc/PID/task/*; do grep -H Cpus_allowed_list "$task/status"; done
```

`cluster/continuous_campaign.py` remains a compatibility command for retained
manifests and older operator notes; new automation should use the campaign
module above.

## Wired data network

All ten machines (the eight `.101`–`.108` workers, Merlin and gawain `.156`)
have permanent `10.203.0.X/24` addresses on the same 1 Gb/s switch. pellinore
`.152` was retired on 2026-10-04 (its GPU was no faster than a P600 at the
highest energy per tile): it is out of the manifest, listed in the leader
setting `retired_nodes` so the dashboard does not report it as down, and
powered off. Its addresses below are kept for reference. (`X` is their Wi-Fi address suffix). Their wired NetworkManager profiles
have no gateway or DNS; Wi-Fi remains the default route, SSH fallback, and
leader control address. `.106`, `.152` and `.156` use a separate
`King Hamming wired` profile (on `.152` and `.156` bound to the USB ASIX
adapter, `enx…`, with autoconnect priority 10) so the preexisting profiles stay
untouched; the other seven use `Wired connection 1`.

Measured on 2026-10-04, fetching a 218 MB blob from `.101`'s agent: Wi-Fi gave
19 MB/s (`.152`) and 26 MB/s (`.156`) with ~7.5 ms ping; the switch gave
105 MB/s on both with ~1.5 ms ping.

Each wired agent advertises both its public `192.168.4.X` blob URL and a
`10.203.0.X` URL in private group `kh-switch`. A worker in that group tries
same-group Ethernet sources first, then public Wi-Fi URLs; a Wi-Fi-only worker
never receives the isolated Ethernet URL. When at least three healthy wired
nodes have disk capacity, a Wi-Fi agent is not assigned a copy of an artifact
already held on the wired network. If wired capacity drops below the artifact's
replica target, cross-group copying remains possible.

After a deliberate full-agent quiesce, use `resume-workers` to guard stopped
dispatch, redeploy the current runtime, recover a verified-absent leader if
necessary, and restart only missing agents on their *existing* blob roots. The
leader revalidates retained blobs and republishes their public URLs; do not
rewrite `replicas` in SQLite by hand. To change the private subnet mapping,
first verify each address on its host, then while idle/stopped run:

```sh
python3 king_hamming/dp_solver/launch_dp.py \
  --state king_hamming/cluster/deployments/continuous-campaign \
  set-storage-addresses --group kh-switch 192.168.4.101=10.203.0.101
python3 king_hamming/dp_solver/launch_dp.py \
  --state king_hamming/cluster/deployments/continuous-campaign resume-workers
```

The manifest records the mapping for all ten machines. To add one host
while the campaign is busy, add its entry to `private_networks` in the manifest
and run `upgrade-worker-rolling --host` for it; `set-storage-addresses` refuses
while any lease is active. `resume-workers`
leaves dispatch stopped; resume the campaign and its feeder separately after
checking all agent registrations and storage validation.

The feeder derives its ready-tile target from the current healthy compute count
(never below the configured floor), and can admit additional independent roots,
up to a hard cap of eight, when dependency-ready tiles are too sparse to fill
the cluster. Status reports both the observed demand and the dynamic target. It
collects exact KHD1 outputs, submits
admitted distributed matching at higher priority, archives verified KHM1, and
tries the next primitive polynomial only after a certified Hall obstruction.
Matching uses one persistent native compute thread per assigned CPU. Each
compute thread is pinned to one distinct CPU, while all threads share the large
read-only field and matching state instead of duplicating it in per-core
processes. New matching submissions use configurable 2/4/full group tiers by
request count; existing queued specifications retain their original group.
DP tiles likewise use pinned multicore teams. A ready tile receives up to two
free host CPUs by default, leaving room for other tile processes to fetch and
publish while one computes. The scheduler fences each team's CPU set and
aggregate reserved memory; an explicit `max_cpus` in a distributed DP
specification can override the team width. Old retained tile specifications
also use the smaller default without changing results. At equal manual
priority, ready tiles from roots with fewer active leases are scheduled first,
so a long-running root does not indefinitely starve another.
Status shows `allocated=N/M`; this is reserved capacity, not measured CPU use.
Use the thread view or canary above to measure activity. One native process can
use all eight CPUs (fourteen on Merlin, twelve on Gawain); each compute
thread still has a one-CPU affinity. Native matching workers pull bounded
chunks from a shared work queue, and DP diagonals spread central and boundary
cells across the team.
Dependency barriers, startup, transfers and matching group assembly can still
cause idle intervals; allocation is not a guarantee of 100% utilization.
Workers prefer verified local predecessor packets and share a bounded 4 GiB
per-node dependency cache. A finished tile also publishes small edge bands
(about 1 MiB against a 25 MiB packet), so a successor downloads about 1.5 MiB
instead of about 65 MiB; the whole packet remains the fallback. The leader prefers
giving a tile to a node that already holds its left neighbour, only as a bounded
tie-break. See [DP_NETWORK_LOCALITY.md](DP_NETWORK_LOCALITY.md).

Every machine has a GPU. Fields that fit an advertised GPU (Merlin's 3060: up to
~180 M labels; the eight P600s: ~55 M) are matched by the single-GPU `match_gpu`
program instead, larger ones block by block (`match_gpu_blocks`, up to 2^36 labels), and DP tile
leases use a free host GPU opportunistically with byte-identical results; see
[GPU.md](GPU.md).

The campaign admits up to a conservative 6 GiB native allocation,
which includes `7^9` and `5^11` on the current hosts.
It pauses DP expansion at four unmatched, currently matchable ready fields or
below 20 GiB of free leader storage. A retained DP result that exceeds matching
memory, edge, or field limits does not apply backpressure: the feeder continues
expanding the DP frontier and preserves that result for a future matching
layout or larger cluster. The scheduler deliberately accumulates idle nodes for a
high-priority nine-node matching instead of starving it behind DP tiles.

## Disk reclamation

The campaign keeps its own disks from filling, without operator action:

- **Finished fields lose their tiles.** Once a field's result is complete and held
  on two live machines for `tile_retention_seconds` (default 6 hours), its tile
  packets and edge bands, and those of its failed attempts, are deleted on every
  machine. Fields still being worked on keep all of theirs. To keep tiles longer,
  or forever (`-1`), change the leader's setting:
  `UPDATE settings SET value='-1' WHERE key='tile_retention_seconds'`.
- **Surplus copies are trimmed.** Every artifact is kept on `target_replicas`
  (three) healthy machines. Extra copies are dropped, least free disk first, never
  below the target, and a machine that is silent for under `replica_grace_seconds`
  (default 10 minutes) is not replaced, so brief Wi-Fi drops stop making extra copies.
- **Replication assignments are exclusive.** The leader gives one agent an
  expiring, renewable claim on each artifact transfer. A failed transfer releases
  its claim; a crashed or disconnected agent loses it automatically. The second
  live copy of a finished tile in an active DP root is served before background
  third copies, because that second copy unlocks dependent tiles. Transfers do
  not hold the agent's storage-wide lock, so tile publication can proceed beside
  a download. The two-live-copy rule for dependent tiles is unchanged.
- **A full disk gets no new work.** A machine reporting less than
  `disk_floor_bytes` (default 10 GiB; `0` turns it off) is given no new leases and
  no new copies, and shows "low disk" in the status and the dashboard. Its running
  work, its checks and its cleanup continue.
- **Scratch is removed.** An agent deletes a run's work directory when the run's
  result is stored, and sweeps leftover directories of finished runs hourly.
  `KH_KEEP_SCRATCH=1` in an agent's environment keeps them for debugging.

The dashboard's Fleet tab shows the effect. Details and measurements are in
[CAMPAIGN_NOTES.md](../web/CAMPAIGN_NOTES.md), items 22–24. Both the leader and the
agents need upgrading (`launch_dp.py upgrade-leader` and `upgrade-workers`) for
these to take effect.

## Control

```sh
python3 king_hamming/cluster/kh.py \
  --leader http://192.168.4.151:8061 stop --all
python3 king_hamming/cluster/kh.py \
  --leader http://192.168.4.151:8061 resume --all

# Replace the owned leader, all worker runtimes, and the retained feeder only
# after every root and child run is idle. The command refuses a race, preserves
# work/blob roots, verifies worker bundle identities, and deliberately leaves
# dispatch stopped.
python3 king_hamming/dp_solver/launch_dp.py \
  --state king_hamming/cluster/deployments/continuous-campaign drain
# Wait for active leases to finish normally before upgrading.
python3 king_hamming/dp_solver/launch_dp.py \
  --state king_hamming/cluster/deployments/continuous-campaign upgrade-workers
```

Stopping dispatch preserves the SQLite queue, worker blobs, checkpoints, and
artifacts. Run-scoped `pause-run`, `resume-run`, `cancel`, and `reprioritize`
commands are also available through `kh.py`.

The DP feeder now expands a configured prime-by-odd-exponent region
(`frontier_max_prime`, `frontier_max_exponent`) by diagonals. DP supports
`p^r` above the 32-bit field limit, up to its checked 64-bit size and 32-bit
budget bounds. Such DP outputs remain available for future matching work, but
the present field builder and matchers cannot consume them. The current
frontier and per-tile memory, visit, tile-count, and disk admission checks
still decide whether a particular larger field is actually queued. Its old dense
`max_state_bytes` estimate is not an admission limit for distributed roots:
it describes the unpartitioned table, not the memory of one tile. Each new
candidate must fit a tile layout under `max_tile_bytes`, stay below
`frontier_max_visits`, and fit a conservative three-copy uncompressed
projection in worker-reported free storage after reserving space for active
roots. Unknown worker disk capacity blocks new fields. The status demand
includes the remaining projected disk budget and skipped fields. The configured
region defaults to primes through 19 and odd exponents through 11; unsupported
fields and layouts are skipped. These settings do not raise matching limits.

A failed DP tile is retried inside its current root after 30, 60, then 120
seconds. The failed run remains in history, while its tile slot receives a new
lease, possibly on the same healthy machine. Only a fourth consecutive failure
of that coordinate fails the root. Completed, replicated tiles stay attached
throughout; successful tiles clear their own failure streak. Previously
failed roots retain the existing feeder retry behavior and can reuse durable
tiles from earlier attempts. Matching memory, edge count, and field size only
decide whether a completed DP artifact can enter matching; they never suppress
DP calculation. `field limit` means matching is deferred, not discarded.
