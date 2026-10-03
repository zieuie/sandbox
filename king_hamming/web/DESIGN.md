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

Planned later: disk usage on the Fleet tab (see "Planned: disk usage" below),
a verifier row viewer, and the library changes in CAMPAIGN_NOTES.md.

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

## Planned: disk usage on the Fleet tab

**Goal:** show, for each machine and for the whole cluster, how its disk
divides into four categories:

| Category | Meaning |
| --- | --- |
| **Tiles** | DP tile packets in the agent's blob store |
| **Other king_hamming** | everything else the campaign keeps: other blobs (matching inputs, checkpoints), run scratch in `work/`, earlier deployments, and on merlin the deployment directory and backups |
| **Unrelated** | everything on the filesystem not owned by king_hamming, including the root-reserved blocks |
| **Free** | space the campaign's user can still write |

### Where the numbers come from

The leader's database has only part of this: each node's
`storage_free_bytes` as of its last heartbeat, and which tiles it holds
(`replicas` joined to `artifacts.size`). It has no disk size, nothing about
non-blob files, and it goes stale as soon as the agents stop. That was the case
on 2026-10-02, when the disks had filled and the campaign was stopped.

**Recommended: a separate disk collector.** A background thread in the
dashboard, independent of snapshot builds, measures each machine directly:

- **Hosts:** a fixed list in the dashboard's configuration (`.101`–`.108`,
  `.151`, `.152`), never addresses read from the database. Each host runs one
  fixed command over `ssh -o BatchMode=yes -o ConnectTimeout=5`, with no
  operator input in it. Merlin is measured locally.
- **The command:** returns JSON with:
  - `statvfs` of the storage root's filesystem: size, free, and available
    to the user
  - `du -s --block-size=1` of `~/.local/share/king_hamming`; one `du`
    invocation, so hard links between `blobs/` and the dependency cache are
    counted once
  - a listing of `blobs/` with each file's name, allocated bytes and inode
- **Classification:** the dashboard classifies blobs itself, against the
  leader's database:
  - a hash whose artifact belongs to a `dp_tile` run, or is a band listed in
    `tile_bands`, counts as **tiles**
  - matching artifacts, checkpoint members and unknown hashes count as
    **other**
  - unknown hashes are also counted separately as *unregistered*
  - each inode is counted once
- **Arithmetic:**
  - `free` = available bytes
  - `tiles` = classified tile bytes
  - `other` = king_hamming `du` total, plus merlin's repository data, minus
    tiles
  - `unrelated` = size − free − tiles − other

  The root reservation (about 5%, 11 GB per worker) falls in unrelated, and
  the tooltip names it.
- **Cadence:** every 30 minutes, plus an operator-only **Measure disks now**
  button (rate-limited to once per minute). The page's Refresh doesn't trigger
  it, so polling never causes SSH storms. A `du` over about 40,000 blobs takes
  a few seconds per machine, and machines are measured one at a time.
- **Persistence and failures:**
  - the last result for each machine is kept in
    `~/.local/share/king_hamming/web/disk.json`, so a dashboard restart shows
    data at once, labelled with its age
  - an unreachable machine keeps its last numbers, greyed, with the error
- **Leader comparison:** where a fresh heartbeat exists, the leader's
  `storage_free_bytes` is shown beside the measured value in the tooltip.

**Later, once CAMPAIGN_NOTES item 15 lands:** agents would report disk size
and deployment bytes in heartbeats. The collector then only fills the gaps
while agents are down.

### Display

- **Machine cards:** a stacked horizontal bar under the memory bar, ordered
  tiles | other king_hamming | unrelated | free, with free as the empty track.
  The caption reads `Disk 233 GB · 43 GB free · measured 12 m ago`. Hovering
  shows:
  - the four values in GB and percent
  - tiles split into *finished fields*, *unfinished fields* and *failed or
    cancelled attempts*, with the largest fields, for example
    `13⁹ 112 GB · 11⁹ 21 GB …`
  - average copies per tile, against the target of 3
  - other king_hamming split into blobs, `work/` and earlier deployments
- **Cluster summary:** at the top of the Fleet tab, the same bar summed over
  all machines, with the one legend for the page, and then two lines:
  - the reclaimable figures from CAMPAIGN_NOTES items 19–21: finished
    fields' tiles, copies above target, and run scratch
  - the projected need of paused or queued roots, against the total free
- **Colours:** tiles in the strong accent; other king_hamming in a lighter
  tint of the same hue, so the campaign reads as one block; unrelated in
  neutral grey; free as the bar track. Labels never rely on colour alone. The
  tooltip and legend name each segment, and the palette is checked with the
  dataviz validator in light and dark.
- **Problems tab:** an alert when a machine's available space drops below
  `max(20 GB, 10%)`, or below the largest tile packet of an active root. A
  critical alert when it reaches zero, since agents then fail writes.
- **Phone width:** the bar keeps its four segments, and the caption wraps.

### Code

- `disk.py`: the collector thread, the remote command, classification, and
  `disk.json`.
- `snapshot.py`: merges the latest disk results into `fleet`. It runs no SSH
  during a build.
- `server.py`: starts the collector, adds `--disk-hosts` and
  `--disk-interval`, and `POST /api/disk/measure` (operators, CSRF).
- `static/fleet.js` and `style.css`: the bar, the summary and the tooltips.
- `problems.py`: the low-disk alerts.
- **Tests:** classification against fixture databases, including hard-linked
  and unregistered blobs; the arithmetic, including the root reservation;
  stale and unreachable hosts; and the measure command's rate limit. None of
  them run SSH.

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
