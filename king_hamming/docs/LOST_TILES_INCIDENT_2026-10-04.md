# Incident: finished DP tiles cleared on every worker upgrade (2026-10-03 to 2026-10-04)

For about 16 hours, every fleet-wide worker upgrade made the leader believe nearly all
finished DP tiles had been lost. It cleared them from the grid and queued them for
recomputation, while their files sat intact on the workers' disks. On 7¹³ about 21,000
finished tiles were cleared at once, and about 9,700 tiles were computed more than once
before it was caught. No result was ever wrong; the cost was wasted compute and misleading
throughput figures.

## Timeline

| When | What |
|---|---|
| 2026-10-03 20:33 | `13d7fc6` lets a tile's successors start from **one** live copy of it, instead of waiting for two (the second copy trailed by about five minutes). Since a tile could now briefly exist on one machine only, the same commit adds a **lost-tile rule**: a finished tile with no live copy for longer than a grace period is cleared and recomputed. |
| Evening and night | Several `upgrade-workers` rollouts. Each one silently cleared most finished tiles. |
| 2026-10-04, morning | Zooey: "7^13 looks crazy, what happened to so many of the tiles?" The 7¹³ grid showed holes everywhere. |
| 12:57 | `6969f38`: the rule fixed, the damage repaired, the leader restarted live. |
| 13:17 | `3fe046a`, `6ba0603`: safeguards (a circuit breaker, an upgrade check, a fleet-restart test, dashboard warnings). |
| 16:20 | `0a1c487`: the root cause removed (restarted workers' copies are kept, marked unverified). |
| Evening | First live tests: gawain's and pellinore's agent restarts, then all ten machines. No tile was cleared. |

## What went wrong

When a worker's agent restarts (as every `upgrade-workers` does), it gets a new session.
The leader can't assume the worker's disk still holds what it did, so it re-checks it:

1. On re-register, the leader **deleted** every replica (copy) record of that worker and
   queued the files for revalidation (`node_revalidation`).
2. The agent then re-checked its files in batches; each file that passed was recorded as a
   copy again. For a big store this takes minutes.

The new lost-tile rule only looked at copy records: "no live copy for longer than the grace
period" meant lost. During a fleet-wide upgrade every worker restarts at once, so for a few
minutes almost no finished tile had any copy record. The rule's grace period only protected
copies on workers that had gone *silent* (it compared heartbeats); the restarted workers were
heartbeating normally, their records were simply missing, so tiles finished more than a grace
period ago were cleared on the first scheduler pass.

The rule was tested, but only with one worker going silent. Nothing simulated a fleet-wide
restart.

## Why it went unnoticed

The cluster looked productive. Recomputed tiles count as completions, so tiles per minute
stayed high, and several performance changes that day were judged by that figure; they
counted recomputation as progress. Only the 7¹³ grid's holes gave it away. Comparing
completions per hour with tiles still recorded as done showed that only 13% of tiles finished
before the last rollout were still counted, against 100% of those finished after it.

## What it cost

| Field | Cleared and restored | Finished more than once |
|---|---|---|
| 7¹³ | about 21,100 | 9,672 |
| 29⁷ | 1,684 | 2,153 |
| 31⁷ | 0 | 1,070 |

The duplicated work was mostly CPU and GPU time. Every earlier rollout since `13d7fc6`
contributed, including the one for the 13⁹ matching.

## The fix, in four layers

**1. The rule (`6969f38`).** A tile is no longer considered lost while a live worker still
has its file queued for revalidation. A tile is cleared only if nobody holds it and nobody is
checking it, or its checker has itself gone silent past the grace period.

**2. Repairing the damage (`6969f38`).** `restore_cleared_tiles` (in `dp_solver/distributed.py`)
points every cleared tile back at its newest finished child run whose file still has a copy.
Applied once to the live campaign: 7¹³ went from 15,156 to 36,462 of 40,804 tiles done. An
audit afterwards found every tile ever finished in the three active fields still had a copy
on disk. 61 tiles on 29⁷ were being recomputed although a finished copy existed: the 47 not
yet started were pointed back at their finished results and the duplicate jobs cancelled, and
the 13 already running were left to finish. That cleanup was a one-off edit to the live
database, not a commit.

**3. Safeguards against the whole class of bug (`3fe046a`, `6ba0603`).**
- **Circuit breaker.** Clearing finished work is the only destructive step the scheduler
  takes on its own, and it rests on bookkeeping that can be wrong in bulk. If one pass would
  clear more than 1% of a field's finished tiles (and more than 50), it clears nothing,
  records a hold in the setting `tile_clear_hold:<run_id>`, and logs why. To let it proceed
  when the copies really are gone, raise `tile_clear_max_fraction`.
- **Upgrade check.** `upgrade-workers` counts each field's finished tiles before, and again
  45 seconds after the workers re-register. It warns if the count fell or a hold appeared,
  and records both counts in `last_rollout.json`.
- **Fleet-restart test** (`cluster/tests/test_tile_safeguards.py`). It re-registers every
  agent on a 169-tile field and asserts that nothing is cleared. Against the code from before
  the fix it cleared 168 of the 169 tiles.
- **Dashboard.** The Problems tab shows a critical item while a hold is active. Each field
  card on the DP tiles page shows how many tiles finished more than once, so recomputation
  can't hide behind completion counts again.

**4. The root cause (`0a1c487`).** Deleting the copy records on re-register was the real
mistake: it made "not yet re-checked" indistinguishable from "gone" for every reader, not just
the lost-tile rule. The same design had earlier caused replication to re-copy everything a
restarted worker held. Now the records stay, marked `verified=0`, until the check finishes:
- Readers that *read bytes* (fetch sources, dependency readiness, retiring finished fields,
  trimming surplus copies) use verified copies only.
- Readers that *decide something is missing* (the lost-tile rule, copy targets, dashboard
  alerts) count unverified copies on live machines as present.
- A copy that fails its check is deleted then, and the dashboard warns if a live worker's
  check runs for more than an hour.

The first live tests were the agent restarts on gawain and pellinore and then all ten
machines on 2026-10-04: thousands of copies went unverified and back to verified within
seconds, none were deleted, no hold appeared, and nothing was re-copied.

## Follow-up: a race in the fix (found and fixed the same night)

Once the agents' logs carried timestamps, every worker turned out to be logging 10 to
25 "replication will retry: no valid blob source: HTTP Error 404" lines a minute. Layer 4
had a race with the retirement of finished fields' tiles:

1. A worker restarts, and its copy records are marked unverified, each queued for a re-check.
2. The finished field's tiles are retired: every holder's record of each tile file,
   unverified ones included, is deleted, and the files are queued for deletion.
3. The re-check, still holding its list, finds the file on disk and reports it valid; the
   leader re-records it as a verified copy.
4. The worker's cleanup deletes the file. Nothing removes the revived record.

The result was 3,680 "verified" copies of files that no longer existed, all edge bands of
the finished 7¹³ field (the retirement scan never revisits bands once their tile is gone),
and replication trying to fetch them forever. No live field was affected, because
retirement is the only step that deletes unverified records and it only touches finished
fields.

Fixed on 2026-10-04:
- Queuing a copy for deletion (`retention.queue_trim`) cancels its pending re-check, and a
  re-check result for a cancelled or deletion-queued copy is ignored instead of recorded.
- A worker's confirmation that it deleted files (`/v1/gc-done`) drops its records of them,
  so a stale record can't outlive its file whatever the cause.
- `distributed.retire_orphan_bands` (run once by hand; about a second) queued the 3,680
  stale records for deletion.
- Tests reproduce the race (`cluster/tests/test_unverified_copies.py`); they fail on the
  code from before the fix.

## Lessons

- **Don't delete a claim you merely haven't re-checked.** Keep it, marked unverified, and
  decide per reader whether "unknown" counts as present.
- **Bulk destructive actions need a breaker.** When thousands of finished items seem to
  vanish at once, the bookkeeping is far more likely to be wrong than the disks.
- **Test the fleet-wide case.** One silent worker and every worker restarting at once are
  different failure modes.
- **A new state needs every writer audited, not just every reader.** Layer 4 decided per
  *reader* how to treat unverified copies, but a *writer* (tile retirement) could delete one
  while its re-check was in flight. Ask what each step that adds, removes or re-records the
  state does when another step is halfway through.
- **Measure net progress, not completions.** Distinct finished units gained per hour would
  have exposed this immediately; completions per minute hid it.

These are also rows in [CLUSTER_PROGRAMMING_LESSONS.md](CLUSTER_PROGRAMMING_LESSONS.md).

## If the hold alert fires

"N of M finished tiles look lost, so none are being recomputed" means one scheduler pass
wanted to clear a large share of a field's finished tiles.
1. Check that the workers that made them are up and finishing their revalidation (the
   Problems tab also warns about checks running over an hour).
2. If machines are down, bring them back; the hold clears by itself once their copies count
   again.
3. Only if the copies are really gone, raise `tile_clear_max_fraction` in the leader's
   `settings` table to let the tiles recompute.
