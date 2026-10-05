# Running DP on five household workers

> **Historical (2026-09-27).** The first five-worker DP campaign. Its deployment (`cluster/deployments/dp-campaign`) no longer exists; the live campaign is described in [CONTINUOUS_CAMPAIGN.md](CONTINUOUS_CAMPAIGN.md).

The campaign launched on 2026-09-27 uses merlin/uther (`192.168.4.151`) as
leader and these compute/storage workers:

| Address | Host | Solver threads |
| --- | --- | --- |
| 192.168.4.101 | fearless | 4 |
| 192.168.4.102 | red | 4 |
| 192.168.4.103 | lover | 4 |
| 192.168.4.104 | folklore | 4 |
| 192.168.4.105 | evermore | 4 |

The leader reserves one physical core, including its SMT sibling. Agents choose
CPU affinity automatically; each solver uses four pinned threads sharing its
state. Workers `.106` through `.108` are available for other work.

The initial queue contains 51 odd-degree prime powers, ordered by estimated raw
DP work. Each calculation is admitted with at most 3 trillion raw transition
visits and 16 GiB of logical dense state. Distributed tiles have side 512 and a
2 GiB process memory limit. Final splits are portable, compressed KHD1 files.
This is a bounded batch: workers remain online for storage and new submissions
when it finishes. The table does not automatically expand beyond that frontier.

## Commands on the leader

From the repository root:

```sh
# Start a NEW deployment; the existing campaign is already running.
python3 king_hamming/cluster/launch_dp.py start

# Inspect the existing campaign.
python3 king_hamming/cluster/launch_dp.py status

# Refresh detailed progress every minute.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 status --watch 60

# Stop calculations across the cluster, retaining results and restart state.
python3 king_hamming/cluster/launch_dp.py stop

# Resume dispatch and computation.
python3 king_hamming/cluster/launch_dp.py resume

# Copy newly completed compact results onto the leader for review.
python3 king_hamming/cluster/launch_dp.py collect
```

A deliberate stop does not request any machine reboot. It prevents dispatch and
signals active solvers. Distributed workers retain complete tiles; unfinished
tiles can be recomputed after resume. Agent storage and replication remain
online. These commands control this deployment's queue, not unrelated programs.

`start` refuses to replace an existing deployment. For a separate campaign,
choose another local state directory and leader port:

```sh
python3 king_hamming/cluster/launch_dp.py \
    --state king_hamming/cluster/deployments/another-campaign \
    start --port 8043 --hosts 192.168.4.106 192.168.4.107 192.168.4.108
```

Pass the same `--state` before `status`, `stop`, `resume`, or `collect` to operate
that campaign. Running the launcher without arguments prints help and examples.

## Retained state and exact launch commands

Local state is in [`deployments/dp-campaign/`](../cluster/deployments/dp-campaign/):

- `leader.sqlite`: the queue, dependency graph, run history and storage index;
- `leader.log`: control-plane diagnostics;
- `manifest.json`: deployment identity, SSH preflight, process PIDs/start times,
  exact leader/agent commands, and all submitted specifications;
- `results/`: collected `.khdp` files and `index.json`, including dimensions,
  score, hash and filename for each retained run.

Each worker retains its runtime bundle, mutable work, content-addressed blobs
and `agent.log` under
`~/.local/share/king_hamming/DEPLOYMENT_ID/`. The actual path is recorded in the
manifest. Runtime files are copied over SSH; workers do not need a shared
filesystem or this repository installed. Detached processes continue after the
launch command or SSH session exits. No system service is installed.

To print the exact individual commands used in the current deployment:

```sh
python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('king_hamming/cluster/deployments/dp-campaign/manifest.json').read_text())
print('Leader:', manifest['leader_process']['command'])
for worker in manifest['workers']:
    print(worker['host'], worker['command'])
    print('Log:', worker['root'] + '/agent.log')
PY
```

A recorded command can also be run manually in a terminal on its machine after
the previous instance exits. The SQLite index and worker storage must be kept
for restart. Inspect logs before replacing a failed process. Automatic host
reboot is not enabled in this deployment.

## More calculations and retained reruns

Preview a larger fastest-first frontier, then submit it:

```sh
python3 king_hamming/cluster/kh.py campaign --distributed \
    --threads 4 --tile-side 512 --max-visits 10000000000000 --limit 100

python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    campaign --distributed --threads 4 --tile-side 512 \
    --max-visits 10000000000000 --limit 100 --submit
```

`campaign` currently submits JSON final outputs; the launcher selects KHD1.
Different specification settings create distinct calculations, so the JSON
frontier can overlap the launcher's binary frontier. To submit a particular
compact calculation, use a JSON specification such as:

```json
{
  "program": "dp_distributed",
  "arguments": {
    "p": 13,
    "r": 5,
    "threads": 4,
    "tile_side": 512,
    "max_tile_bytes": 2147483648,
    "max_visits": 3000000000000,
    "artifact_format": "KHD1"
  }
}
```

Save it as `my_dp.json`, then run:

```sh
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue my_dp.json

# An explicitly prioritized calculation moves ahead of ordinary queued work.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue my_dp.json --priority 10

# Retain the previous run while computing a fresh attempt.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue my_dp.json --from-scratch
```

The launcher's `collect` selects the runs recorded in its manifest. Manually
submitted runs are visible through ordinary `kh.py status`, including their
artifact download URLs.

## Inspecting results

After collecting, list filenames in `deployments/dp-campaign/results/` and print
one with the production utility:

```sh
python3 king_hamming/dp_solver/print_dp.py PATH_TO_RESULT.khdp
python3 king_hamming/dp_solver/print_dp.py PATH_TO_RESULT.khdp --verify
```

Collection validates SHA-256, canonical binary encoding, dimensions and split
feasibility. `--verify` additionally recomputes the optimum and ordered split
independently, subject to its configurable `--max-visits` limit. Collected names
include run IDs so distinct attempts are preserved. Final artifacts are also
replicated on workers; collection retains those copies.

Intermediate tile files remain retained in this draft. If every replica of a
tile disappears, its dependents currently wait rather than automatically
recomputing it. See [`QUEUED_TILES.md`](QUEUED_TILES.md) for recovery boundaries.
