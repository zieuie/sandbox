# DP solver commands

Run these commands from the repository root. The C programs require Linux,
a C11 compiler, Make, and pthreads; the inspection tools require Python 3.
See [DESIGN.md](DESIGN.md) for algorithms, representations, and module boundaries.

## Build and check

```sh
make -C king_hamming/dp_solver
make -C king_hamming/dp_solver check
```

Every executable prints help and an example when invoked without arguments.

## Estimate a calculation

```sh
king_hamming/dp_solver/kh_estimate 5 3 --json
king_hamming/dp_solver/kh_estimate 5 3 --profile-transitions --json
```

## Compute a local DP split

```sh
king_hamming/dp_solver/kh_dp_local 5 3 \
    --threads 2 \
    --work-dir /tmp/kh-dp-5-3 \
    -o /tmp/split_5_3.json
```

Progress is printed as JSON lines on stdout; diagnostics go to stderr.
To stop, press Ctrl-C or send SIGTERM. The solver finishes its current tile,
checkpoints, and exits with code 75. Run the same command to resume from the
retained work directory. Thread count can change on resume.

Choose a fresh output filename for a completed rerun: existing outputs are never
overwritten. `--checkpoint-seconds 1800` and `--progress-milliseconds 10000`
are the default checkpoint and report intervals.

## Print, verify, and convert results

```sh
python3 king_hamming/dp_solver/print_dp.py /tmp/split_5_3.json
python3 king_hamming/dp_solver/verify_dp.py /tmp/split_5_3.json

# Convert to portable compressed binary.
python3 king_hamming/dp_solver/print_dp.py /tmp/split_5_3.json \
    --binary-out /tmp/split_5_3.khdp

# Print and independently verify binary output.
python3 king_hamming/dp_solver/print_dp.py /tmp/split_5_3.khdp --verify
```

Independent verification has a work limit; use `--max-visits N` to raise it.
Conversion also refuses to overwrite an existing output.

## Build a field partition

```sh
king_hamming/dp_solver/kh_field 5 3 --threads 2
king_hamming/dp_solver/kh_field 5 3 --threads 2 -o /tmp/field_5_3.bin
```

The command generates a primitive polynomial and prints JSON metadata and
sample cells. The optional native binary table needs that metadata for reuse.
For example, save both together:

```sh
king_hamming/dp_solver/kh_field 5 3 --threads 2 \
    -o /tmp/field_5_3_saved.bin > /tmp/field_5_3_saved.json
```

## Operate the household DP campaign

```sh
python3 king_hamming/dp_solver/launch_dp.py status
python3 king_hamming/dp_solver/launch_dp.py collect
python3 king_hamming/dp_solver/launch_dp.py stop
python3 king_hamming/dp_solver/launch_dp.py resume
```

The existing campaign database and results remain in
[`cluster/deployments/dp-campaign/`](../cluster/deployments/dp-campaign/).
The `cluster/launch_dp.py` command remains a thin compatibility wrapper.
To start a fresh default campaign, use `launch_dp.py start`; it refuses to replace
an existing deployment. Use `--state DIRECTORY` and a different `start --port`
for another campaign, as described in the [cluster README](../cluster/README.md).

## Add workers and extend the current queue

```sh
python3 king_hamming/dp_solver/launch_dp.py add-workers \
    --hosts 192.168.4.106 192.168.4.107 192.168.4.108

python3 king_hamming/dp_solver/launch_dp.py extend \
    --max-visits 30000000000000 --limit 100
```

`add-workers` skips hosts already recorded in the campaign and retains each new
worker's launch command and storage path. It does not restart existing workers.
`extend` adds new prime powers in estimated-runtime order, skipping mathematical
entries already present in the manifest or visible queue, including completed
results. It retains the old specifications, runs, and collected files. `--limit`
counts newly added calculations. Repeating the same frontier adds no duplicate
entries. Both commands accept the launcher's global `--state` option.

The active household deployment now uses `.101` through `.108`. The current
frontier admits up to 30 trillion raw visits per newly added calculation; the
existing entries retain their previous admission limits. Tile memory remains
limited to 2 GiB per process with four solver threads.

## Enqueue and watch

```sh
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 \
    enqueue king_hamming/dp_solver/dp_distributed_5_3.json
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8041 status --watch 60

# Preview the configured DP frontier without submitting it.
python3 king_hamming/cluster/kh.py campaign --distributed \
    --threads 4 --tile-side 512 --limit 20
```

Add `--submit` to the campaign command to enqueue its entries; provide `--leader`
before `campaign` when using a nondefault leader URL.
Set `"artifact_format": "KHD1"` in a distributed specification to request portable
binary output. The household launcher already selects this format.

## Run the standalone tile driver

```sh
python3 king_hamming/dp_solver/tile_driver.py 5 3 \
    --work-dir /tmp/kh-dp-tiles-5-3 --tile-side 7 \
    --workers 2 --threads 1 -o /tmp/kh-dp-tiles-5-3.json
python3 king_hamming/dp_solver/print_dp.py /tmp/kh-dp-tiles-5-3.json --verify
```

All adapter command programs print useful help without arguments. The standalone
driver has its own retained index; it does not submit work to the ordinary queue.

## Analyze retained split templates

```sh
python3 king_hamming/dp_solver/analyze_templates.py \
    king_hamming/examples king_hamming/cluster/deployments/continuous-campaign/results
```

The JSON inventory records atom counts, budget residues, score, coset count, and
exact run order without expanding solver tables. It supports template conjectures
and counterexample searches; it does not replace optimality verification.
