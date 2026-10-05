# Concerns about the DP side of the campaign

> **Snapshot (2026-10-02).** Some items have since been addressed: the "long GPU hold" fix in item 2 is deployed, and the DP now runs mostly on GPUs ([MACHINE_CONTRIBUTIONS.md](MACHINE_CONTRIBUTIONS.md)). Recheck an item against the code before acting on it.

Written 2026-10-02 (night), after deploying block matching and resuming the DP roots.
Each item says what I **observed**, what I only **suspect**, and what to look at. Nothing here
is urgent; the campaign was healthy when this was written.

State at the time: the DP roots 13^9 (73% of its cells) and 23^7 (16%) running tiles on nine
machines; four large block matchings (2^29, 19^7, 7^11, 11^9) running on four of the same machines.

## Observed

### 1. Paused roots are silent
13^9 and 23^7 sat **paused for about a day** with every machine idle, and nothing said so. The
dashboard showed the machines as idle and the feeder said "matching backpressure", which
sounded like the cause. The real cause was two `paused` rows that only `kh.py resume-run`
(not `resume --all`) un-pauses.
- A paused root is invisible as a *reason* on the Fleet cards (idle reason says "no queued work").
- Suggestion: show paused roots in the Feeder panel and have the idle reason say "2 DP roots
  are paused". Possibly make `resume --all` offer to resume them.

### 2. A long GPU hold makes every tile on that host wait for nothing
A block matching holds the host's GPU lock for its whole run (minutes to hours on a P600).
Each DP tile on that host waits up to `KH_GPU_DP_WAIT_SECONDS` (120 s) for the lock and then
runs on the CPU anyway. Roughly a 2-minute penalty per tile on every host running a matching.
- Fixed in code (2026-10-02): the block bridge marks its hold "long" and tiles skip the wait.
  **Not deployed**: it needs an agent upgrade, which needs idle agents.
- Still open: the lock is all-or-nothing. Holding it per block (not per run) would let tiles
  interleave between blocks, at the cost of the kernel releasing and re-taking device memory.

### 3. A big matching takes a whole machine away from DP
A lease reserves memory, and the leader places work only where the reservation fits beside the
reservations already there. 11^9 reserves 13.3 of dp-106's 13.5 usable GiB, so dp-106 runs no
tiles at all while it matches. That is the intended accounting, but it means four of ten machines
contribute little DP throughput for the duration of the matchings.
- **Suspected, not seen:** a big job can starve behind a stream of small tile leases on a busy
  host, because the leader tries each queued job in priority order and skips any that do not fit
  yet; nothing holds slots back for it. The four matchings all landed within minutes, but they were
  queued when most hosts had few reservations.

### 4. DP production is coupled to the matching backlog
The feeder adds no new DP roots while `max_ready_fields` (4) fields are waiting for matching. With
five large fields blocked on matching for days, DP production was throttled by a matching limitation.
Now that GPU matching is fast the backlog should drain, but the coupling stays: any field that
cannot be matched (7^11 and 11^9 were "field limit" until today) still counts toward the limit.
- Look at `matchable_backlog` in `campaigns/king_hamming.py`.

## Suspected, not measured

### 5. Reconstruction at 13^9 scale has not been exercised since the 22-24 changes
When a root's last tile finishes, its reconstruction runs on one machine, on the CPU only (the GPU
helps with tiles, not reconstruction). 13^9 is the largest root so far (137.9 G cells). The
starvation fix (item 19) and the retention changes (22-24) are new, and nothing has yet gone through
a full reconstruction with them. This is the first thing I would watch when 13^9 finishes.
- Watch: which host gets it, its RAM, and whether tiles of other roots are drained away for it
  (`reconstruction_drain_target` in `cluster/leader.py`).

### 6. How much the GPU helps tiles on the fleet is unmeasured
The overnight GPU work measured tiles at 30 s on a GPU against 115 s on a CPU for 13^9 (first hour,
five machines). Nine slots per host share one GPU through one lock, and a P600 has 1.7 GiB usable.
I have not seen the GPU utilisation of a host running a full DP load. The new GPU graph on the Fleet
cards (agents sample `nvidia-smi` every heartbeat) will show it. If the GPU sits well below 100%
with tiles waiting on CPU, the lock or the slot count is the limit, not the GPU.

### 7. Network, still Wi-Fi
Unchanged and deferred by you: all machines are on Wi-Fi, and the roaming outage of 2026-10-02
(`docs/NETWORK_OUTAGE_2026-10-02.md`) is unfixed. Each tile result is kept on three machines (average
2.99 copies measured), so DP traffic is mostly replication. If the outage recurs, an agent loses its
lease and its tiles are redone.

## Checked and fine

### 8. Disk
Measured from the leader's database: 13^9's finished tiles take about 130 GiB per copy at 73% done,
projecting to roughly 530 GiB over three copies at completion; 23^7 about 290 GiB. Together about
830 GiB against 1.4 TB free (93 to 278 GiB per machine; the lowest is dp-107 at 93 GiB). The 10 GiB
free-disk floor on new leases will stop work before a disk fills. My first rough arithmetic suggested
a shortfall; the measurement says otherwise.

## Not DP, but worth keeping next to these
- **Leader authentication (item 18)** is still deferred: the leader listens on 0.0.0.0 with no login.
- **Block matching on 7^11 and 11^9** is running for the first time at this scale on a P600.
  Their results are verified by the independent native verifier before they are accepted.
