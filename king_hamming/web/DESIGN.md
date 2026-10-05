# Campaign dashboard: design

A read-only web dashboard for the continuous DP → matching campaign. It runs
as its own process on merlin (`.151`) and never contacts the leader, so a
dashboard bug cannot disturb the campaign.

**Status:** built and in daily use. It has eight tabs (Results, Fleet, DP tiles,
Matching, Timeline, Feeder, Problems, Activity), the login and the operator commands
(described in README.md). It is reachable from the internet through a Cloudflare tunnel
(`cloudflared`, running as a system service on merlin) that points at `127.0.0.1:8070`.

## Scope

Built, all read-only:

1. **Results heatmap**: prime × exponent, coloured by outcome.
2. **Fleet**: one card per machine.
3. **DP tile grids**: one live grid per active DP root.
4. **Machine timeline**: 24 h of leases per machine (`timeline.py`).
5. **Feeder panel**: pipeline policy, gauges, fields in flight, next fields,
   and pass history (`feeder.py`).
6. **Problems feed**: current conditions plus 7 days of grouped events
   (`problems.py`). Leader and feeder log lines carry their own timestamps
   (`cluster/logstamp.py`), which `logs.py` reads, so this history is exact and
   survives dashboard restarts; older unstamped lines are timed by when the
   dashboard first saw them.
7. **Matching**: live and past matching runs, with phase-by-phase convergence
   from the leader's `checkpoints` table, where `cursor` is the phase and
   `done` is the number of matched requests (`matching.py`).

Also built: operator commands (`commands.py`) and detached process jobs
(`jobs.py`, `jobrunner.py`).

Also built: disk usage on the Fleet tab ("Disk usage on the Fleet tab" below).
Planned later: low-disk alerts on the Problems tab, a verifier row viewer, and
the library changes in CAMPAIGN_NOTES.md.

## Architecture

```
browser ──(Cloudflare tunnel)──▶ web/server.py (127.0.0.1:8070)
                                                       │  reads only
                     ┌─────────────────────────────────┼──────────────────────┐
                     ▼                                 ▼                      ▼
   deployments/continuous-campaign/       pipeline.json, results/,   dp_solver/tiles.py
   leader.sqlite  (?mode=ro)              matching-results/          campaigns/result_table.py
```

- **Standard library only**: `http.server.ThreadingHTTPServer`, `sqlite3`,
  `hashlib.scrypt`, `secrets`, like the rest of the repo.
- **Front end**: modern JavaScript, ES modules, inline SVG for charts and
  `<canvas>` for the tile grids. No framework and no build step.
- **Binds to `127.0.0.1` by default.** `--listen 0.0.0.0:8070` opens it to
  the home network. Only the tunnel faces the internet. The leader (port 8061)
  and agent storage ports must stay LAN-only.

## Data and caching

The server builds one **snapshot** and holds it in memory:

- **No request waits for a rebuild.** Once the snapshot is older than `--ttl`
  seconds (default 15), the next request still gets it at once, while a
  background thread builds a new one. Only the very first build after a start
  (begun at startup, before any request) and a forced **Refresh** (at most once
  every 3 s) wait.
- The page polls every 30 s while it is visible, and every view shows the
  data's age.

Each section (status, fleet, roots, results) is built independently. If one
fails, its previous value stays up with a warning and the others still update.

Immutable inputs are memoized across builds, keyed by path, size and mtime:

- DP artifacts
- matching-certificate checks
- decoded inline matching DPs
- tile dependency geometry

**Certificate checks run off the critical path.** Hashing every archived
matching certificate (about 40 GB, including 20 GB for 13⁹) took 2–3 minutes,
and used to block the first build after every restart. A background worker now
checks them, and results are saved in `certificates.json` in the state
directory, so a restart re-hashes nothing it has already checked. Until a
certificate's check finishes, its field shows "Matched, certificate being
checked".

**Costs on the live campaign (2026-10-04):** a build takes about 7.5 s of CPU,
mostly the tile grids of three large DP roots (about 200,000 tiles). The
snapshot is about 3.7 MB of JSON, 0.6 MB compressed. It used to be 21.8 MB
(4.9 MB compressed), almost all of it per-tile detail; that detail now lives
behind `/api/tiles` (below).

### Snapshot contents

**`results`**: covers every retained deployment and reuses
`certificate_status` and `permutation_count` from `campaigns/result_table.py`.
Unlike `records()`, one uncollected artifact becomes a note on that field
instead of aborting the whole table. Active runs on a leader with no live
heartbeat are labelled "(leader offline)". It merges in `pipeline.json` for
each field:

- `p, r, q, θ, rows, requests, edges`
- DP state and attempts (run id, state, error)
- matching attempts (polynomial, state, outcome)
- matching admission (`admitted`, `field limit`, …)
- notes (uncollected artifacts, hash mismatches, gave-up matching)

**`fleet`**: one record per row of the `nodes` table, plus:

- the host alias (`dp-101` → fearless `.101`, …, `dp-151` → merlin `.151`)
- heartbeat age, state, `idle_reason` (the leader's rules, re-evaluated from
  the read-only database)
- CPU set and slots, memory, runtime version
- current leases: field, tile row/column, phase, progress, assigned CPUs,
  reserved memory
- a 24 h CPU-utilisation series in 15-minute buckets, built from
  `resource_usage_samples` (Δcpu ÷ Δt ÷ CPU count, summed over that node's
  leases)
- 24 h lease occupancy: the union of `lease_history` intervals, plus leases
  where the node ran a matching shard
- `orphaned` on any running tile whose DP root has already reached a terminal
  state

**`roots`**: for each `dp_distributed` root that is active, still has
running or queued tiles, or finished in the last 24 h:

- field, attempt number, grid size, counts
  (durable/complete/running/ready/blocked/failed), tiles finished in the last
  hour, and tiles finished more than once (`recomputed`)
- the first dependency boundary
- a compact **grid string**: one character per tile, row by row (`STATE_CODES`
  in `snapshot.py`: `d` durable, `c` complete, `r` running, …), plus a short
  list of the tiles that are live right now (running or queued: machine,
  progress)

The full per-tile detail (state, node, child run, timings, copies) is built
with the snapshot but sent only on request: `GET /api/tiles?run=<root>` returns
it in columnar form for one root (fetched when you open that field), and
`GET /api/tile?run=<root>&r=<row>&c=<column>` returns one tile.

A tile is shown *durable* when it is complete and has at least 2 live copies;
the scheduler itself only needs one (`dependency_replicas`, default 1). Copies
a restarted worker has not yet re-checked count as live (see
`docs/LOST_TILES_INCIDENT_2026-10-04.md`). A tile that hasn't been created yet
is *blocked* or *ready* according to `dp_solver.tiles.dependencies`. A single
grouped query does the replica counting, instead of one query per tile.

A machine listed in the leader setting `retired_nodes` (a JSON list) is left out
of `fleet` and of the header's machine count while it is silent, instead of being
reported as down; the disk monitor stops measuring it and drops its numbers from
the cluster totals.

## HTTP endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/login`, `/static/{login.html,login.js,style.css,icon.svg}` | public | Login page |
| GET | `/api/health` | public | `{"ok": true}` only |
| POST | `/api/login` | public, throttled | Check the password; set the session cookie |
| GET | `/`, `/static/*` | session | App shell (signed out: redirect to `/login?next=`) |
| GET | `/api/session` | session | User, role, CSRF token, re-confirmation deadline |
| GET | `/api/snapshot` | session | The cached snapshot as JSON (gzip when accepted) |
| GET | `/api/tiles?run=` | session | Per-tile detail of one DP root, columnar (gzip when accepted) |
| GET | `/api/tile?run=&r=&c=` | session | One tile's detail |
| GET | `/api/audit` | session | Last 200 audit entries |
| POST | `/api/refresh` | session + CSRF | Force a rebuild (debounced to once per 3 s) |
| POST | `/api/reauth` | session + CSRF, throttled | Re-enter the password; opens a 10-minute window |
| POST | `/api/password` | session + CSRF | Change your own password; signs you out everywhere |
| POST | `/api/logout` | session + CSRF | End this session |
| POST | `/api/disk/measure` | operator + CSRF | Measure every machine's disk now (at most once a minute) |

Commands, built:

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/api/command/preview` | operator + CSRF | `{name, params}`, returning title, changes, items, warnings, blockers, `confirm_text`, `reauth`, `fingerprint` |
| POST | `/api/command/run` | operator + CSRF | `{name, params, fingerprint, confirm}`; responses below |
| GET | `/api/jobs` | session | Recent jobs (with output tails), git state, `last_rollout.json` |

`/api/command/run` responds in one of these ways:

- 409 with a fresh preview, when the fingerprint is stale or a blocker applies
- 400 when the typed confirmation is wrong
- 403 with `reauth_required` when the password needs re-entering
- 502 when the leader is unreachable

Every command is built from fresh state as a *plan*. The facts the plan depends
on are hashed into its fingerprint, which is how staleness is detected. The
dashboard runs one command at a time.

## Authentication (built: `auth.py`, `audit.py`)

Differences from the original plan:

- **Cookie mode:** the cookie is `SameSite=Lax`, not Strict. Following a link
  to the dashboard from chat or email then keeps you signed in. GET requests
  never change anything, and every POST needs the CSRF token.
- **`Secure` flag:** set automatically for HTTPS requests from a trusted proxy,
  or always with `--secure-cookies`. It is left off for plain-HTTP access on
  the LAN, where browsers would otherwise drop the cookie.
- **Unwanted POSTs:** blocked three ways:
  - an `Origin` / `Sec-Fetch-Site` check
  - a requirement that bodies are `application/json`, which plain HTML forms
    from other sites cannot send
  - the per-session CSRF token
- **Re-confirmation:** `/api/reauth` opens a 10-minute window. Signing in
  counts as one. Risky commands will check it.

- **Users file**: `users.json` lives in a state directory outside the repo
  (default `~/.local/share/king_hamming/web/`, mode 0600), so secrets are never
  committed. Each user has a name, an scrypt hash with salt, and a role
  (`viewer` now, `operator` reserved for the controls). Manage it with
  `python3 web/server.py add-user NAME`, which prompts for the password, and
  `remove-user NAME`.
- **Sessions**: 256-bit random tokens. Only their SHA-256 is stored, in
  `sessions.sqlite`, so sessions survive restarts. Sessions expire after 7
  days, or 24 h idle.
- **Session cookie flags**: `HttpOnly; SameSite=Lax; Path=/`, plus `Secure`
  on HTTPS (see above).
- **CSRF**: a per-session token sent in an `X-CSRF-Token` header on every
  POST. Logout and Refresh use it now, so it is already in place when controls
  arrive.
- **Brute-force protection**: after a failed login, delays grow per IP and per
  username, and accounts lock temporarily after repeated failures. The client
  IP is taken from `X-Forwarded-For` only when `--trust-proxy` is set.
- **Security headers**: a strict Content-Security-Policy (`default-src
  'self'`, no inline script), `X-Frame-Options: DENY`, `nosniff`, and
  `Referrer-Policy: no-referrer`.
- **Static files** come from a fixed allow-list, so a crafted path cannot reach
  other files on disk.

### HTTPS

Decided: the existing domain and Cloudflare Tunnel will terminate TLS, with
the tunnel pointing at `127.0.0.1:8070`. Cloudflare Access can optionally add
a second login layer; the dashboard's own login still applies. Behind the
tunnel the client IP comes from `CF-Connecting-IP`.

## Front end

`index.html` loads `app.js` (an ES module). The page has:

- **Header**: dispatch, feeder and machine status, the data's age, a
  Refresh button and the signed-in user.
- **Tabs**: Results · Fleet · DP tiles · Matching · Timeline · Feeder ·
  Problems · Activity. The selected tab, and an optional detail, are kept in
  the URL hash (`#results/2,29`).
- Each view module exports `render(container, snapshot, detail)` and rebuilds
  its panel from the data into a fresh container. Nothing else carries state.
- Every interpolated value goes through the escaping `html` tagged template in
  `util.js`. There is no inline script or style, so the CSP holds. That includes
  style *attributes*: the CSP's `style-src 'self'` blocks them, so dynamic
  sizes and colours are set through the CSSOM (`element.style.setProperty`).

The views:

- **Results**: an SVG/HTML grid with primes as rows and exponents as columns.
  Each cell shows the row count (abbreviated, e.g. `1.47T`) and a colour:
  matched, obstructed, matching queued/running, DP complete but too big to
  match, DP running, DP failed. Clicking a cell opens a detail drawer with the
  exact numbers, every attempt, polynomials and hashes.
- **Fleet**: one card per machine (ten since pellinore was retired). Each card
  shows the hostname and address, a health dot, heartbeat age, the idle reason
  or current work, a row of CPU squares (lit when assigned), a memory bar, its
  GPUs, 24 h CPU and GPU utilisation sparklines, disk usage and the 24 h
  occupancy %.
- **DP tiles**: one `<canvas>` grid per root, drawn from the grid string, with a
  colour per state. Grids of finished fields are drawn only when their section
  is opened. Hovering shows a magnifier (a zoomed view of the surrounding tiles)
  and the tile's details; clicking selects a tile. Above each grid is a counts
  summary, including tiles finished more than once. Drawing the three active
  roots (about 200,000 tiles) takes about 110 ms.
- **Matching**: live and past runs. A GPU block run shows its stages (GPU
  wait, field rows, blocks, exchange, write, publish, verification) with
  per-stage progress, time left and a stacked timeline, and a live burndown of
  unmatched requests.
- **Activity**: the operator commands, including drain, stop, resume and
  dispatch status.

The layout works at phone width. Colours come from CSS variables, with a dark
theme.

## Disk usage on the Fleet tab (built: `disk.py`)

Each machine's disk, and the cluster's, divide into four categories: **tiles**,
**other king_hamming data**, **unrelated** files and **free** space. README.md
describes what each counts. How it works:

- **A separate collector.** The leader's database knows only each node's
  `storage_free_bytes` as of its last heartbeat, and goes stale as soon as the
  agents stop, which is when disk matters most. So `DiskMonitor` measures the
  machines itself, in a background thread independent of snapshot builds.
  `Snapshots` only merges the last result into the fleet (`fleet.disk`, and a
  `disk` entry on each card); it never starts a measurement.
- **One fixed script per machine** (`REMOTE_SCRIPT`), sent on stdin to
  `python3 -` over `ssh -o BatchMode=yes`, or run directly for this machine. The
  host list is `HOST_NAMES`; each machine's blob-store path comes from the
  leader's `nodes` table and must match a strict pattern. It reports the
  filesystem's size and free space (`statvfs`), allocated bytes under the blob
  store, `work/`, the rest of the deployment, other deployments and (here) the
  repository's `cluster/deployments` and `cluster/backups`, counting each inode
  once, and a list of `(digest, bytes)` for the canonical blobs.
- **Classification** is done by the dashboard (`tile_index`): a blob is a tile if
  its hash is the artifact of a `dp_tile` run, or a band in `tile_bands`. A tile
  shared by several attempts takes the best state of its roots (finished, then
  unfinished, then failed). The same pass counts copies per tile, giving the
  average and the bytes above the target.
- **Arithmetic** (`breakdown`): `free` is the space available to the user,
  `tiles` the classified blob bytes, `other` everything king_hamming keeps
  minus tiles, and `unrelated` the rest of the disk, which includes the
  root-reserved blocks (reported separately in the tooltip).
- **Cadence and failures:** every 30 minutes (`--disk-interval`), and on request
  through `POST /api/disk/measure` (operators, audited, at most once a minute). A
  machine that fails keeps its last numbers with the error and its age. Results
  persist in `disk.json`.
- **Display** (`static/fleet.js`): a stacked bar on each card, with the figures in
  text beside it so colour is never the only cue, and a cluster bar above the
  cards with the legend and the reclaimable estimates. Tiles are the strong
  accent, other king_hamming a lighter tint of it, unrelated neutral grey and
  free the empty track, with variants for dark mode. The two tooltips share one
  handler with the CPU squares, because a second `tooltips()` call on the same
  container hides the first one's tooltips.

Not yet built: the Problems-tab alert when a machine's free space runs low
(below `max(20 GB, 10%)`, or below the largest tile packet of an active root, and
critical at zero), and the projected need of paused or queued roots against the
free total. Once CAMPAIGN_NOTES item 7 lands, agents would report disk size and
deployment bytes in their heartbeats, and the collector would only fill the gaps
while agents are down.

## Files

```
web/
  DESIGN.md        this document
  README.md        how to run and test
  server.py        CLI (serve, snapshot), HTTP routing, security headers
  snapshot.py      read-only queries, snapshot building and caching
  timeline.py      lease segments, merging and lanes
  matching.py      matching runs, phase checkpoints, machine groups, resources
  feeder.py        pipeline panel and the feeder process check
  problems.py      current conditions and grouped recent events
  logs.py          incremental, rotation-safe log watching; /proc process checks
  auth.py audit.py accounts, sessions, throttling; append-only audit log
  commands.py      command plans: preview, fingerprint, blockers, execution
  jobs.py jobrunner.py   detached process jobs with recorded outcomes
  disk.py          disk measurement: remote script, classification, monitor thread
  CAMPAIGN_NOTES.md      library changes that would give better control
  static/
    index.html  style.css  icon.svg  app.js  util.js
    results.js  fleet.js  tiles.js  timeline.js  feeder.js  problems.js
    login.html  login.js  account.js  command.js  activity.js  matching.js
  tests/
    fixture.py  test_snapshot.py  test_server.py  test_views.py  test_auth.py  test_commands.py
    test_disk.py  test_matching_stages.py
  Makefile         `make -C king_hamming/web check`
```

Authentication added `auth.py`, `audit.py`, `static/login.html`,
`static/login.js`, `static/account.js` and `tests/test_auth.py`, plus the
`add-user`, `passwd`, `set-role`, `remove-user`, `list-users` and
`revoke-sessions` commands.

Tests run against small SQLite fixtures generated in a temporary directory,
never against the live database.

## Deploying front-end changes: stale browser caches

Static files are served under fixed URLs (`/static/tiles.js`, …), so a browser may
keep running cached JavaScript after a dashboard restart. That is harmless until the
snapshot's *format* changes: on 2026-10-04 the DP tiles tab stopped loading because a
cached `tiles.js` expected per-tile cells while the new server sent grid strings. A
hard refresh (or clearing the cache) fixes it. If a format change ships again, either
tell users to hard-refresh, or add versioned static URLs (for example
`/static/v/<code version>/tiles.js`); that was considered and not adopted.

