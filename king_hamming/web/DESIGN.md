# Campaign dashboard: design

A read-only web dashboard for the continuous DP → matching campaign. It runs
as its own process on merlin (`.151`) and never contacts the leader, so a
dashboard bug cannot disturb the campaign.

**Status:**

- Built: six views (results, fleet, DP tiles, timeline, feeder, problems), the
  Activity tab, the login, and the commands. The commands are described in
  README.md.
- It is not yet exposed to the internet. Authentication, specified below, must be
implemented before the Cloudflare tunnel points at it.

## Scope

Built, all read-only:

1. **Results heatmap**: prime × exponent, coloured by outcome.
2. **Fleet**: one card per machine.
3. **DP tile grids**: one live grid per active DP root.
4. **Machine timeline**: 24 h of leases per machine (`timeline.py`).
5. **Feeder panel**: pipeline policy, gauges, fields in flight, next fields,
   and pass history (`feeder.py`).
6. **Problems feed**: current conditions plus 7 days of grouped events
   (`problems.py`), with log lines timed by `logs.py`.
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
browser ──(later: Cloudflare tunnel)──▶ web/server.py (127.0.0.1:8070)
                                                       │  reads only
                     ┌─────────────────────────────────┼──────────────────────┐
                     ▼                                 ▼                      ▼
   deployments/continuous-campaign/       pipeline.json, results/,   dp_solver/tiles.py
   leader.sqlite  (?mode=ro)              matching-results/          campaigns/result_table.py
```

- **Standard library only**: `http.server.ThreadingHTTPServer`, `sqlite3`,
  `hashlib.scrypt`, `secrets`, like the rest of the repo.
- **Front end**: modern JavaScript, ES modules, inline SVG. No framework and
  no build step.
- **Binds to `127.0.0.1` by default.** `--listen 0.0.0.0:8070` opens it to
  the home network. Once exposed, only the tunnel faces the internet. The
  leader (port 8061) and agent storage ports must stay LAN-only.

## Data and caching

The server builds one **snapshot** and holds it in memory:

- It rebuilds on demand once the snapshot is older than `--ttl` seconds
  (default 15).
- **Refresh** forces a rebuild, at most once every 3 s.
- The page polls every 30 s while it is visible, and every view shows the
  data's age.

Each section (status, fleet, roots, results) is built independently. If one
fails, its previous value stays up with a warning and the others still update.

Immutable inputs are memoized across builds, keyed by path, size and mtime:

- DP artifacts
- matching-certificate checks
- decoded inline matching DPs
- tile dependency geometry

Costs measured on the live campaign: a cold build takes about 5 s (hashing
about 400 MB of certificates), a warm build about 0.7 s, and the gzip payload
is about 150 KB.

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
  (durable/complete/running/ready/blocked/failed)
- the first dependency boundary
- one cell per `distributed_tiles` row: state, node, child run id, live
  replica count

A tile is *durable* when it is complete and has at least 2 live replicas, the
same rule as `dp_solver/adapter.py`. A tile that hasn't been created yet is
*blocked* or *ready* according to `dp_solver.tiles.dependencies`. A single
grouped query does the replica counting, instead of one query per tile.

## HTTP endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/login`, `/static/{login.html,login.js,style.css,icon.svg}` | public | Login page |
| GET | `/api/health` | public | `{"ok": true}` only |
| POST | `/api/login` | public, throttled | Check the password; set the session cookie |
| GET | `/`, `/static/*` | session | App shell (signed out: redirect to `/login?next=`) |
| GET | `/api/session` | session | User, role, CSRF token, re-confirmation deadline |
| GET | `/api/snapshot` | session | The cached snapshot as JSON (gzip when accepted) |
| GET | `/api/audit` | session | Last 200 audit entries |
| POST | `/api/refresh` | session + CSRF | Force a rebuild (debounced to once per 3 s) |
| POST | `/api/reauth` | session + CSRF, throttled | Re-enter the password; opens a 10-minute window |
| POST | `/api/password` | session + CSRF | Change your own password; signs you out everywhere |
| POST | `/api/logout` | session + CSRF | End this session |

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

- **Header**: dispatch, feeder and machine status, the data's age, and a
  Refresh button. The signed-in user is added with authentication.
- **Tabs**: Results · Fleet · DP tiles. The selected tab, and an optional
  detail, are kept in the URL hash (`#results/2,29`).
- Each view module exports `render(container, snapshot, detail)` and rebuilds
  its panel from the data into a fresh container. Nothing else carries state.
- Every interpolated value goes through the escaping `html` tagged template in
  `util.js`. There is no inline script or style, so the CSP holds.

The views:

- **Results**: an SVG/HTML grid with primes as rows and exponents as columns.
  Each cell shows the row count (abbreviated, e.g. `1.47T`) and a colour:
  matched, obstructed, matching queued/running, DP complete but too big to
  match, DP running, DP failed. Clicking a cell opens a detail drawer with the
  exact numbers, every attempt, polynomials and hashes.
- **Fleet**: nine cards. Each card shows the hostname and address, a health
  dot, heartbeat age, the idle reason or current work, a row of CPU squares
  (lit when assigned), a memory bar, a 24 h utilisation sparkline and the 24 h
  occupancy %.
- **DP tiles**: one SVG grid per active root, with a colour per state. Running
  tiles are labelled with the machine's short name, the dependency boundary is
  given as text, and hovering shows a tooltip. Above each grid is a counts
  summary. A failed root whose tiles are still running shows an alert.

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
  Makefile         `make -C king_hamming/web check`
```

Authentication added `auth.py`, `audit.py`, `static/login.html`,
`static/login.js`, `static/account.js` and `tests/test_auth.py`, plus the
`add-user`, `passwd`, `set-role`, `remove-user`, `list-users` and
`revoke-sessions` commands.

Tests run against small SQLite fixtures generated in a temporary directory,
never against the live database.
