# Campaign dashboard

A read-only web view of the continuous DP → matching campaign: a results
heatmap, the nine-machine fleet, live DP tile grids, a machine timeline, the
feeder, and a problems feed. It reads the retained
campaign files and never contacts the leader. See [DESIGN.md](DESIGN.md).

Every page needs a login. Accounts, sessions and the audit log live in
`~/.local/share/king_hamming/web/` (mode 0700), outside the repository.

## Accounts

```sh
python3 king_hamming/web/server.py add-user zooey --role operator   # prompts for a password
python3 king_hamming/web/server.py add-user friend                  # read-only viewer
python3 king_hamming/web/server.py passwd zooey          # also signs that user out everywhere
python3 king_hamming/web/server.py set-role friend operator
python3 king_hamming/web/server.py list-users            # accounts and active sessions
python3 king_hamming/web/server.py revoke-sessions friend
python3 king_hamming/web/server.py remove-user friend
```

Passwords need at least 10 characters. Users can also change their own
password from the account menu, at the top right of the page.

- **Roles:** `viewer` can see everything. `operator` will be able to run
  commands once they exist. Risky commands will also ask for the password again
  (it counts for 10 minutes).
- **Sessions:** a sign-in lasts up to 7 days, or 24 hours without use.
- **Failed sign-ins:** after 4 failures within 15 minutes, from one address or
  for one name, each further attempt is delayed, doubling up to 15 minutes.
- **Audit log:** every login, logout, re-confirmation and password change is
  recorded in `audit.jsonl`.

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

Options:

- `--deployments DIR`: default `king_hamming/cluster/deployments`.
- `--campaign NAME`: the live deployment; default `continuous-campaign`.
- `--ttl SECONDS`: how long a snapshot is reused; default 15.
- `--state-dir DIR`: where accounts, sessions and the audit log live.

## Behind the Cloudflare tunnel

1. Keep the dashboard on loopback and point `cloudflared` at
   `http://127.0.0.1:8070`.
2. Start the dashboard with `--trust-proxy`. It then takes the visitor's
   address from `CF-Connecting-IP` (for throttling and the audit log), and marks
   the session cookie `Secure` on HTTPS requests. Those headers are only
   trusted from a connection on this machine.
3. Optionally, add Cloudflare Access in front as a second sign-in.

```sh
python3 king_hamming/web/server.py serve --trust-proxy
```

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

- **Matching** (`#matching`, or `#matching/RUN_ID` to open one run):
  - **Now:** each live matching run, showing its polynomial, its machine group,
    how many requests are matched, the phases committed, and solver health.
  - **Convergence chart:** requests still unmatched after each phase, on a log
    scale, from the per-phase checkpoints the leader keeps. While a run is
    live, a dashed segment shows progress since the last checkpoint.
  - **Waiting for matching:** completed DP results not yet matched, with the
    exact limit that holds each one back.
  - **History:** every past run, with outcome (matched, or a Hall obstruction
    and how many requests short), phases, duration, machines and peak memory.
    Click a row for its convergence chart and per-shard CPU and memory.
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

## Commands (operators only)

Buttons appear next to whatever they act on, and every command works the same
way:

1. **Preview:** the dialog shows exactly what will change: which runs, and the
   current and new values. It also lists warnings, and **blockers**, which
   refuse the command outright.
2. **Confirm:** destructive commands ask you to type a word, such as `stop` or
   a field like `103^3`. Risky ones ask for your password again; that counts
   for 10 minutes.
3. **Stale check:** if anything the preview showed has changed by the time you
   click Run, the command is refused and a fresh preview appears.
4. **Audit:** every command, successful or not, goes into the audit log, which
   the **Activity** tab shows.

| Where | Command | What it does |
| --- | --- | --- |
| Fleet header, Activity | Stop / Resume dispatch | Leader `/v1/control`. Stopping asks running work to stop at its next checkpoint and requeue (type `stop`). |
| Fleet card, tile click | Pause / Resume, Priority… | Leader `/v1/run-command`. Cancel is offered for matching and roots, **not** for single tiles (see below). |
| DP tiles card | Pause / Resume field | Pauses the root, so no new tiles start; running tiles finish. |
| DP tiles card | Restart field… | Cancels the attempt and its live tiles, then submits a new attempt that reuses every durable tile. This is how one cancelled or stuck tile gets recomputed. |
| DP tiles card | Cancel field… | Cancels the root and its live tiles (type the field, password). |
| DP tiles card, Problems | Cancel leftover tiles | Cancels tiles still running under a failed root. |
| Feeder, Problems | Retry… | Submits a new attempt for a field the feeder gave up on, recorded in `manifest.json` under the feeder's lock. |
| Feeder | Edit… / Raise visit limit… | Edits `pipeline.json` under the feeder's lock, after the feeder's own `validate_settings` (password). Previews list newly eligible fields. |
| Feeder | Add fields now… | Runs `launch_dp.py extend` while holding the feeder's lock (password). |
| Activity, Problems | Start / Restart feeder | Start runs `launch_dp.py ensure-feeder`; restart calls `restart_owned_feeder` so new feeder code loads (password). |
| Activity | Upgrade workers… / leader… | `launch_dp.py upgrade-workers` / `upgrade-leader` (type the phrase, password). Blocked until no run is active, and the preview lists your uncommitted files. |

**Single tiles can't be cancelled while their root is active.** The leader
never recreates a cancelled tile, so the field could never finish. Pause the
tile instead, or use **Restart field**. Problems flags any root already stuck
this way. [CAMPAIGN_NOTES.md](CAMPAIGN_NOTES.md) lists this and other library
changes that would allow finer control.

**Process jobs** (start/restart feeder, upgrades) run detached from the
dashboard. Each runs through `jobrunner.py`, with its output in
`~/.local/share/king_hamming/web/jobs/`, and keeps going if the dashboard
restarts. Only one job runs at a time. The Activity tab shows live output,
the stage reached by the last rollout, and your git state.

## Test

```sh
make -C king_hamming/web check
```

Tests build small fixture deployments with the leader's own schema in a
temporary directory; they never touch live campaign files.
