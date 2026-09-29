# Cluster commands

Run these commands from the repository root. Agents and the leader require
Python 3.12 or later; the workers also need the C solver binaries. SSH deployment
uses the existing keys. See [DESIGN.md](DESIGN.md) for scheduling and recovery. DP-specific code and
commands live in [dp_solver/](../dp_solver/README.md). Matching commands
live in [matching_solver/](../matching_solver/README.md); legacy launcher paths
below remain compatibility commands.

## Build and check

```sh
make -C king_hamming/dp_solver
make -C king_hamming/matching_solver
make -C king_hamming/cluster check
make -C king_hamming/matching_solver check
```

Every executable prints help and an example when invoked without arguments.

## Operate the existing household campaign

```sh
python3 king_hamming/cluster/launch_dp.py status
python3 king_hamming/cluster/kh.py \
    --leader http://192.168.4.151:8041 status --watch 60

# Stop and resume computation, retaining results and restart state.
python3 king_hamming/cluster/launch_dp.py stop
python3 king_hamming/cluster/launch_dp.py resume

# Copy newly completed compact results to the leader.
python3 king_hamming/cluster/launch_dp.py collect
```

Collected files and their score/hash index are in
[`deployments/dp-campaign/results/`](deployments/dp-campaign/results/).

```sh
python3 king_hamming/dp_solver/print_dp.py PATH_TO_RESULT.khdp
python3 king_hamming/dp_solver/print_dp.py PATH_TO_RESULT.khdp --verify
```

## Start a new household campaign

The default launcher uses leader `.151:8041` and five workers `.101`–`.105`:

```sh
python3 king_hamming/cluster/launch_dp.py start
```

It refuses to replace the existing deployment. For a separate campaign, choose
a fresh directory and port, and specify the workers:

```sh
python3 king_hamming/cluster/launch_dp.py \
    --state king_hamming/cluster/deployments/another-campaign \
    start --port 8043 --hosts 192.168.4.106 192.168.4.107 192.168.4.108
```

Pass the same `--state` before `status`, `collect`, `stop`, or `resume` to operate
that campaign. `manifest.json` records exact launch commands, process identities,
worker paths, and submitted calculations; `leader.log` contains diagnostics.

## Submit individual calculations

```sh
# Whole DP on one worker.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue king_hamming/dp_solver/dp_5_3.json

# One DP distributed across multiple workers.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue king_hamming/dp_solver/dp_distributed_5_3.json

# Prioritize a manual request or retain a fresh attempt.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue my_dp.json --priority 10
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue my_dp.json --from-scratch
```

Duplicate specifications reuse their existing run unless `--rerun` or
`--from-scratch` is supplied. Previous runs remain retained. To request compact
binary output, set `"artifact_format": "KHD1"` in a distributed specification's
`arguments`. Status includes artifact download URLs for manually submitted runs;
launcher collection selects only runs listed in its manifest.

## Submit a matching attempt

A matching job consumes a saved KHD1 split and pins one primitive polynomial.
Use a leader and worker deployment built from the current code bundle; the
existing long-running DP deployment may still have an older bundle.

```sh
python3 king_hamming/matching_solver/submit.py \
    king_hamming/matching_solver/examples/13_5.khdp \
    --poly 2,4,0,0,0,1 --threads 4 \
    --leader http://127.0.0.1:8041 --enqueue
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8041 status
```

Add `--distributed --workers N` to reserve 2-8 agents for one matching;
`--threads C` starts one multithreaded C shard per agent (up to its assigned
CPUs). Python handles leases and stream setup; the matching algorithm,
checkpoints, and certificate bytes stay in C. This requires a freshly deployed bundle.
The run's artifact URL serves a verified KHM1 matching or exact Hall
obstruction. The status endpoint shows completed requests, committed
checkpoints, replica counts, and the result location. Native matching processes
also report lifetime user-plus-system CPU time and Linux peak RSS. The leader
retains those approximate measurements in its `resource_usage` SQLite table,
separately for each lease attempt, coordinator, and shard; `kh.py status` prints
them in seconds and MiB.

## Preview and enqueue a table frontier

```sh
# Preview without contacting the leader.
python3 king_hamming/cluster/kh.py campaign --distributed \
    --threads 4 --tile-side 512 --max-visits 3000000000000 --limit 100

# Submit the previewed frontier.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    campaign --distributed --threads 4 --tile-side 512 \
    --max-visits 3000000000000 --limit 100 --submit
```

The campaign CLI emits JSON results by default; the household launcher requests
KHD1. Different specification settings create distinct calculations.

## Run a local calculation

Use separate terminals. These ports differ from the household campaign's port.

```sh
python3 king_hamming/cluster/leader.py serve \
    --database /tmp/kh-local-demo/leader.sqlite \
    --listen 127.0.0.1:8045 --checkpoint-seconds 1800
```

```sh
python3 king_hamming/cluster/agent.py run \
    --leader http://127.0.0.1:8045 --name local-worker \
    --work-root /tmp/kh-local-demo/work \
    --storage-root /tmp/kh-local-demo/blobs \
    --storage-listen 127.0.0.1:8046 \
    --storage-url http://127.0.0.1:8046
```

```sh
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8045 \
    enqueue king_hamming/dp_solver/dp_5_3.json
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8045 status
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8045 stop --all
python3 king_hamming/cluster/kh.py --leader http://127.0.0.1:8045 resume --all
```

For a leader colocated with a compute agent, add `--pin-leader-core` to the leader
and `--leader-node` to the agent. Verify a downloaded artifact with:

```sh
python3 king_hamming/cluster/verify_artifact.py \
    king_hamming/dp_solver/dp_5_3.json result.bin --sha256 EXPECTED_HASH
```
