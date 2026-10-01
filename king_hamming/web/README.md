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

**Recommended: run `cloudflared` on merlin.** The dashboard then never
listens on the network at all:

```sh
python3 king_hamming/web/server.py serve --trust-proxy --secure-cookies
```

Point the tunnel's public hostname at `http://127.0.0.1:8070`. That is
*Service → HTTP → localhost:8070* in the Zero Trust dashboard, or in a local
`config.yml`:

```yaml
ingress:
  - hostname: kh.example.com            # your hostname
    service: http://127.0.0.1:8070
  - service: http_status:404            # nothing else is reachable
```

**If the tunnel runs on another machine,** the dashboard has to listen on the
LAN, and you name that machine as the only trusted proxy:

```sh
python3 king_hamming/web/server.py serve --listen 192.168.4.151:8070 \
    --trusted-proxy 192.168.4.X --secure-cookies
```

Other LAN devices can then reach it over plain HTTP too (the login still
applies). Use the public HTTPS address from those devices as well.

What the flags do:

- **`--trust-proxy` / `--trusted-proxy ADDRESS`:** take the visitor's address
  from `CF-Connecting-IP`, which Cloudflare sets itself, for throttling and the
  audit log. The header is honoured only on connections from those addresses.
- **`--secure-cookies`:** the session cookie becomes
  `__Host-kh_session; Secure`, usable only over HTTPS on this exact hostname,
  and every response carries HSTS. Local `http://127.0.0.1` still works,
  because browsers treat localhost as secure. Plain-HTTP LAN addresses don't.

Before opening it up:

1. **Put Cloudflare Access in front.** A Zero Trust application for the
   hostname, allowing only your email addresses. Strangers then never reach
   even the login page, and the dashboard's own login becomes a second factor.
2. **Map only this hostname to the dashboard,** with a catch-all 404. Never
   route the leader (8061), SSH (22), rpcbind (111) or agent storage ports,
   and keep no router port-forwards to merlin.
3. **Use a long, unique password** for every operator account.
   `server.py list-users` shows accounts and their sessions, and
   `revoke-sessions NAME` signs one out everywhere.
4. **Don't override the tunnel's "HTTP Host Header" setting.** The dashboard
   rejects POSTs whose `Origin` doesn't match the host it receives, so an
   override shows up as "cross-site request refused" at login.

Built-in hardening:

- passwords hashed with scrypt; at most 4 checked at a time
- doubling delays on failed sign-ins
- CSRF tokens, Origin checks and JSON-only bodies on every POST
- strict CSP with no inline script; framing refused
- static files from an allow-list only
- post-login redirects limited to plain local paths
- at most 64 connections, each cut after 30 s idle
- no software versions advertised
- the audit log is visible to operators only

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
| Results (empty cell, or Submit a field…), Feeder | Submit a field… | Queues a DP root for any supported p^r (p prime, r odd 3–31) and records it in the feeder's manifest, so its result is collected and matched like any other (password). Details below. |
| Activity, Problems | Start / Restart feeder | Start runs `launch_dp.py ensure-feeder`; restart calls `restart_owned_feeder` so new feeder code loads (password). |
| Activity | Upgrade workers… / leader… | `launch_dp.py upgrade-workers` / `upgrade-leader` (type the phrase, password). Blocked until no run is active, and the preview lists your uncommitted files. |

**Submit a field** previews:

- the field size, DP work, state size and priority
- a tile layout: the smallest side (512–16384) that passes the leader's
  limits of at most 10,000 tiles and each tile within `max_tile_bytes`. 11⁹,
  for example, needs side 2048 (6,241 tiles), not the feeder's 512.
- a runtime estimate from the throughput of recently completed roots
- the worker disk needed for both tile copies
- whether the field exceeds the matching limits

Fields beyond the feeder's own limits need the field typed to confirm.
Existing fields are refused; use Retry or Restart field for those.

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

**Stopping a running job:**

- **Stop job…** (password; type `stop job` for upgrades) sends the job's own
  process group a stop signal, and force-stops it after 15 seconds. That group
  includes whatever the job spawned (`make`, `ssh`, …).
- Services the job already started, such as the feeder, leader or agents,
  detach into their own sessions and keep running.
- The job then shows as **cancelled**.
- Stopping an upgrade partway can leave workers on mixed runtimes.
  `last_rollout.json`, shown on the Activity tab, records the stage reached and
  how to recover.

## Test

```sh
make -C king_hamming/web check
```

Tests build small fixture deployments with the leader's own schema in a
temporary directory; they never touch live campaign files.
