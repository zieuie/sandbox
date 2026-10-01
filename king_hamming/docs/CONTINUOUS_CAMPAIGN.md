# Continuous household campaign

The unified campaign owns DP and distributed matching on one leader and one
nine-agent pool: workers `.101` through `.108`, plus Merlin (`.151`) with its
leader physical core reserved. Its retained state is
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
DP tiles likewise use pinned multicore teams. A ready tile receives all free
host CPUs that fit its existing tile memory ceiling, rather than being confined
to one supervisor slot. The scheduler fences the whole CPU set and aggregate
memory for the lease; an explicit `max_cpus` in a distributed DP specification
can cap its tile teams if several smaller processes are desired. Old retained
tile specifications also gain the expanded teams without changing results.
Status shows `allocated=N/M`; this is reserved capacity, not measured CPU use.
Use the thread view or canary above to measure activity. One native process can
use all eight CPUs (fourteen on Merlin); each compute thread still has a
one-CPU affinity. Native matching workers pull bounded chunks from a shared
work queue, and DP diagonals spread central and boundary cells across the team.
Dependency barriers, startup, transfers and matching group assembly can still
cause idle intervals; allocation is not a guarantee of 100% utilization.

The campaign admits up to a conservative 6 GiB native allocation,
which includes `7^9` and `5^11` on the current hosts.
It pauses DP expansion at four unmatched, currently matchable ready fields or
below 20 GiB of free leader storage. A retained DP result that exceeds matching
memory, edge, or field limits does not apply backpressure: the feeder continues
expanding the DP frontier and preserves that result for a future matching
layout or larger cluster. The scheduler deliberately accumulates idle nodes for a
high-priority nine-node matching instead of starving it behind DP tiles.

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
  --state king_hamming/cluster/deployments/continuous-campaign upgrade-workers
```

Stopping dispatch preserves the SQLite queue, worker blobs, checkpoints, and
artifacts. Run-scoped `pause-run`, `resume-run`, `cancel`, and `reprioritize`
commands are also available through `kh.py`.

The DP frontier remains intentionally bounded by configured DP state, visits,
and disk watermarks. Matching memory, edge count, and field size only decide
whether a completed DP artifact can enter matching; they never suppress DP
calculation. `field limit` means matching is deferred for that artifact, not
that its DP result is discarded. Transiently failed DP roots are retried
automatically, up to three attempts, before the feeder expands the frontier.
