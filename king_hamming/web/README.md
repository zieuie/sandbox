# Campaign dashboard

A read-only web view of the continuous DP → matching campaign: a results
heatmap, the nine-machine fleet, live DP tile grids, a machine timeline, the
feeder, and a problems feed. It reads the retained
campaign files and never contacts the leader. See [DESIGN.md](DESIGN.md).

> **No login yet.** Keep it on loopback or the home network. Do not point the
> Cloudflare tunnel at it until authentication (DESIGN.md) is implemented.

## Run

From the repository root, on merlin:

```sh
# Only this machine (open http://127.0.0.1:8070/)
python3 king_hamming/web/server.py serve

# Any machine or phone on the home network (http://192.168.4.151:8070/)
python3 king_hamming/web/server.py serve --listen 0.0.0.0:8070

# Build one snapshot and print a summary (timing, warnings, node and root states)
python3 king_hamming/web/server.py snapshot
```

Options: `--deployments DIR` (default `king_hamming/cluster/deployments`),
`--campaign NAME` (the live deployment; default `continuous-campaign`), and
`--ttl SECONDS` (default 15).

## How fresh is the data?

- **Snapshots:** the server builds one snapshot and reuses it for `--ttl`
  seconds. A cold build takes a few seconds while it reads and hashes every
  retained certificate. Those results are memoized per file, so later builds
  take under a second.
- **Polling:** the page polls every 30 s while its tab is visible.
- **Refresh:** **Refresh** forces a rebuild, at most once every 3 s.
- **Age:** the header shows how old the data is.
- **Errors:** if part of a build fails, the previous data for that part stays
  on screen with a warning banner.

## Views

- **Results** (`#results`):
  - Shows primes × exponents. Each cell holds the exact row count, coloured by
    outcome: matched `^`, obstructed `*`, too big to match, DP running, or DP
    failed.
  - Clicking a cell shows every DP and matching attempt across all retained
    deployments.
  - The URL keeps the open cell, for example `#results/2,29`.
- **Fleet** (`#fleet`): each machine card shows:
  - allocated, free and unschedulable CPUs
  - reserved memory
  - current work with progress and solver health
  - the idle reason, using the leader's own rules
  - 24 h measured CPU use and the fraction of time leased

  Tiles whose DP root has already failed are flagged.
- **DP tiles** (`#tiles`):
  - One grid per active DP root, with tiles marked durable, under-replicated,
    running, queued, ready, blocked, or failed.
  - Also shows the dependency frontier and typical tile time.
  - Roots finished in the last 24 h are folded underneath.

- **Timeline** (`#timeline`, or `#timeline/6h` for 3h/6h/12h/24h):
  - One row per machine, with one bar per lease, coloured by field.
  - Back-to-back tiles of one field are merged into one bar.
  - Overlapping leases on a shared host stack into lanes.
  - Bars that ended badly are outlined.
  - Measured CPU use is shaded behind each row, with the fraction of the
    window leased.
- **Feeder** (`#feeder`):
  - The process (checked against `/proc`), the last pass, and the
    backpressure gauges.
  - Every field still in flight, with DP tile progress, and the next fields the
    feeder would add.
  - Recent passes (identical ones collapsed) and the full limits.
- **Problems** (`#problems`): the tab shows a count of current critical and
  warning conditions.
  - **Needs attention now:**
    - offline machines
    - stalled solvers
    - fields the feeder gave up on
    - tiles still running under a failed root
    - feeder down, stale or failing
    - disk watermark
    - under-replicated results
    - repeated leader errors in the last hour
  - **Recent events (7 days)** are grouped by kind:
    - DP root, tile and matching failures
    - engine retries and expired leases
    - leader log exceptions and feeder errors
  - Info-level events are hidden by default. "New" marks events since your
    last visit in this browser.

The leader and feeder logs have no timestamps. The dashboard reads the
existing log as an untimed baseline (for the leader, only the current
session). Every later line is timed by when the dashboard first saw it, so log
times are only as fine as the page's refreshes, and they restart when the
server restarts.

## Test

```sh
make -C king_hamming/web check
```

Tests build small fixture deployments with the leader's own schema in a
temporary directory; they never touch live campaign files.
