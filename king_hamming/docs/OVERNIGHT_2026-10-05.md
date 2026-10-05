# Overnight report, 2026-10-05

## Summary

**The cluster ran healthily all night, and nothing needs urgent attention.** At 06:40:
- **Leader:** healthy, with no database lock errors all night.
- **Machines:** all 10 heartbeating.
- **Throughput:** about 1,570 tiles an hour, every one on a GPU.
- **Errors and disk:** no replication errors, and disk space unchanged.

Two false alarms in the dashboard turned up overnight; both are fixed and deployed.
Nothing went wrong with the computation itself.

| Field | At midnight | At 06:40 | Rate overnight | Projected DP finish |
|---|---|---|---|---|
| 29⁷ | 53.5% (16,006 of 29,929 tiles) | **71.9%** (21,530) | 827 tiles/h | about **4:50 pm today**, possibly an hour or two later because of its narrow final diagonals |
| 31⁷ | 30.0% (15,313 of 51,076) | **39.7%** (20,276) | 743 tiles/h | roughly **Tuesday morning**: it speeds up to about 1,570 tiles/h once 29⁷ is done |

After its DP, 29⁷ (q = 1.7 × 10¹⁰) needs reconstruction and then a 64-bit GPU block matching
on merlin, like 13⁹ (about an hour, plus 2–3 hours of verification).

### What changed overnight

| When | Commit | What | Deployed |
|---|---|---|---|
| 00:05–00:51 | `41dfd95` (from last night) | Tiles report a heartbeat while waiting for their GPU | All 10 agents |
| 01:05 | `78e3fbd` | **CPU fallback off by default.** Tiles wait for their GPU instead of spending 25–50 minutes on CPUs. There's a toggle on the Activity tab ("DP tiles on CPUs") | Leader, dashboard, all 10 agents by 01:32 |
| 01:05 | `061a2fd` | A tile waiting for its GPU shows as healthy (`waiting-for-gpu`), not "no progress" | Leader, dashboard |
| 05:30 | `c0e2e26` | A change of phase counts as progress, so a tile that has just started its kernel after a long wait isn't flagged | Leader (live restart, no leases lost) |

Since 02:22 every tile has run on a GPU. Running tiles split about 11 computing and 29
waiting their turn for their machine's GPU, which is expected: four tiles share each P600.

### For you to decide

1. **Push.** The branch is 17 commits ahead of `origin` (including this report). My `git push` was blocked by Claude
   Code's auto-mode permission check, so push yourself, or allow `git push` in your settings.
2. **Tile placement plan** ([GPU_TILE_PLACEMENT_PLAN.md](GPU_TILE_PLACEMENT_PLAN.md)). 29⁷
   reaches its narrow tail this afternoon, which is exactly where the plan saves the most
   (an estimated 45–80 minutes per field). Step 1 is a simulator, which doesn't touch the
   cluster. Say if you want me to start.
3. **Leader connection resets** (16–30 an hour, harmless). Agents give up on a request
   before the leader reads its body; they retry. A small tidy-up would catch the reset
   quietly in `read_json`. Optional.
4. **Leftover file:** `~/.local/share/king_hamming/dashboard.log` is an old log location
   unused since 10-02 (the live log is `web/dashboard.log`). Delete it if you like.
5. **Still open from before:** 7¹³'s matching (needs about 200 GB of RAM and lifting the 2³⁶
   cap; see [HARDWARE_BRIEF.md](HARDWARE_BRIEF.md)), and the Wi-Fi control-path risk
   ([NETWORK_OUTAGE_2026-10-02.md](NETWORK_OUTAGE_2026-10-02.md)).

### What each hourly check covered

- **Leader:** health, lock errors per route, write waits.
- **Machines:** heartbeat ages for all 10.
- **Tiles:** throughput over the last hour and which engine ran each, running and queued
  counts, and the health of every running tile.
- **Replication:** 404s on fearless, evermore and gawain.
- **Disk:** free space on merlin and those workers.
- **Services:** the dashboard, and the feeder's liveness and last pass.
- **Progress:** 29⁷ and 31⁷.
- **Logs:** new errors in the leader and feeder logs (timestamped since last night).

The checklist script is `/tmp/kh_overnight_check.py` (read-only).

## Running log

Times are CDT.

- **00:30** Zooey went to bed and asked for hourly checks, with authority to fix problems
  (code changes and machine configuration; nothing online). Checks are scheduled for 1:21,
  2:21, 3:21, 4:21, 5:21 and 6:21; this report is written at 6:40.
  - State at hand-off: the GPU-wait heartbeat rollout (`41dfd95`) is under way, with 2 of
    10 agents upgraded (fearless, red). The CPU-fallback toggle (default off) is built and
    tested in the worktree `/tmp/kh_wt`, waiting for that rollout to finish.
  - Last measured: about 1,560 tiles/h, leader healthy, no lock errors since the writer
    queue went live, replication 404s stopped at 22:59.
  - The branch is 13 commits ahead of `origin`. A `git push` was blocked by the auto-mode
    safety check, so pushing is left to Zooey.
- **00:51** The GPU-wait heartbeat rollout finished on all 10 agents. "heartbeat-missing"
  alerts went from 16 to 0, and 28 tiles reported "waiting for GPU". A smaller false
  alarm remained: tiles waiting more than 5 minutes showed "no-progress-warning", and one
  had waited about 11 minutes, about to fall back to its CPU.
- **00:55–01:05** Landed the CPU-fallback toggle (`78e3fbd`; leader setting
  `dp_cpu_fallback`, off by default; Activity-tab card) and a health fix (`061a2fd`: a
  heartbeating tile waiting for its GPU shows as `waiting-for-gpu`, not as stalled).
  - 300 cluster tests, 113 web tests and the GPU end-to-end check pass. The CPU-assist
    test now turns the setting on, since assist is a use of the CPUs.
  - Leader restarted live (kept 41 leases) and dashboard restarted. Worker rollout for
    the tile solver change started at 01:05.
- **01:23 Check 1: healthy.**
  - **Leader:** healthy, 59,323 writes since its 00:58 restart, no lock errors, worst write
    wait 2.2 s.
  - **Machines:** all 10 heartbeating (oldest 6 s).
  - **Throughput:** 1,563 tiles in the last hour; 38 running, 107 queued. Running tiles:
    12 responding, 26 waiting for their GPU, 1 starting. None heartbeat-missing or stalled.
    3 of 1,563 tiles ran on CPUs (agents not yet upgraded with the toggle).
  - **Progress:** 29⁷ 57.5% (17,195 of 29,929 tiles), 31⁷ 31.9% (16,273 of 51,076).
  - **Errors:** no replication 404s on fearless, evermore or gawain, and no feeder errors
    in the last hour. The leader logged 20 "connection reset by peer" (harmless clients
    hanging up, from agent restarts during the rollout).
  - **Disk:** merlin 207 GB free, workers 182–859 GB. Dashboard and feeder up; the
    feeder's last pass was 2 minutes ago.
  - **Rollout:** the CPU-toggle rollout has reached 5 of 10 agents (fearless, red, lover,
    folklore, evermore).
  - **Fix to my own check:** the first run counted pre-upgrade unstamped log lines as
    "last hour" (they sort after the timestamps). I restricted it to stamped lines.
- **01:32** The CPU-toggle rollout finished on all 10 agents (one runtime version
  everywhere). New tiles report `cpu_fallback: false`, and all 124 tiles in the following
  5 minutes ran on GPUs.
- **02:22 Check 2: healthy.**
  - **Leader:** healthy, no lock errors in 206,511 writes, worst write wait 2.5 s.
  - **Machines:** all 10 heartbeating.
  - **Throughput:** 1,573 tiles in the last hour (1 on a CPU: a tile that started before
    the toggle reached its machine). 40 running (11 computing, 29 waiting for their GPU),
    97 queued.
  - **Progress:** 29⁷ 60.2% (+825 tiles in the hour), 31⁷ 33.3% (+727).
  - **Errors:** no 404s and no feeder errors. Disk unchanged. Dashboard and feeder up.
  - **Looked into:** the leader's steady "connection reset by peer" (about 16 an hour,
    from every mini-PC). They happen while the leader reads a request body: the agent had
    already given up on the request and closed the connection. That's about 0.03% of
    requests, the agents retry, and the dashboard already files them as harmless. Left
    alone; a possible tidy-up is to catch the reset quietly in `read_json`.
- **03:22 Check 3: healthy.**
  - **Leader:** no lock errors in 355,096 writes, worst write wait 2.5 s.
  - **Machines:** all 10 heartbeating.
  - **Throughput:** 1,576 tiles in the last hour, all on GPUs (the first full hour with
    no CPU tiles). 41 running (10 computing, 30 waiting for their GPU), 81 queued.
  - **Progress:** 29⁷ 63.0% (+834), 31⁷ 34.7% (+735).
  - **Errors:** no 404s, no feeder errors, 24 harmless connection resets. Disk unchanged.
    Dashboard and feeder up.
- **04:21 Check 4: healthy.**
  - **Leader:** no lock errors in 502,965 writes.
  - **Machines:** all 10 heartbeating.
  - **Throughput:** 1,572 tiles in the last hour, all on GPUs. 41 running (11 computing,
    30 waiting for their GPU), 80 queued.
  - **Progress:** 29⁷ 65.7% (+803), 31⁷ 36.2% (+757).
  - **Errors:** no 404s and no feeder errors. Disk unchanged. Dashboard and feeder up.
  - **Watched:** the harmless connection resets have crept up each hour (16, 21, 24, 30).
    The leader isn't the cause: it answers in under 1 ms, its database writer is busy 17%
    of the time, and the mean write wait is 26 ms (worst 2.5 s, behind a 2 s scheduler
    pass or a 2.5 s cleanup plan). Still about 0.03% of requests; agents retry. Keeping an
    eye on it.
- **05:22 Check 5: healthy, with one false warning found and fixed.**
  - **Leader:** no lock errors in 652,345 writes.
  - **Machines:** all 10 heartbeating.
  - **Throughput:** 1,581 tiles in the last hour, all on GPUs. 41 running, 82 queued.
  - **Progress:** 29⁷ 68.4% (+828), 31⁷ 37.7% (+756).
  - **Errors:** no 404s and no feeder errors. Connection resets back down to 25.
  - **Found:** one 31⁷ tile on folklore showed "no-progress-warning" while in fact fine.
    It had waited about 5½ minutes for the GPU and its kernel had just started. The
    leader only counted progress when the completed-work counter rose, and the kernel
    restarts it at 0, so the wait looked like no progress.
  - **Fixed** (`c0e2e26`): a change of phase now counts as progress. New test; 301 cluster
    tests pass. Leader restarted live at 05:30 (kept all 40 leases, 3.4 s).
- **06:22 Check 6: healthy.**
  - **Leader:** no lock errors in 142,125 writes since the 05:30 restart.
  - **Machines:** all 10 heartbeating.
  - **Throughput:** 1,567 tiles in the last hour, all on GPUs. 40 running (11 computing,
    29 waiting for their GPU), 84 queued. No warnings of any kind on running tiles.
  - **Progress:** 29⁷ 71.0% (+771), 31⁷ 39.3% (+809).
  - **Errors:** no 404s, no feeder errors, 30 harmless connection resets. Disk unchanged.
    Dashboard and feeder up.
- **06:40 Final check:** healthy. Leader: no lock errors in 188,680 writes since 05:30.
  All 10 machines heartbeating. 1,580 tiles in the last hour, all on GPUs; 39 running,
  85 queued. No 404s, no feeder errors.

