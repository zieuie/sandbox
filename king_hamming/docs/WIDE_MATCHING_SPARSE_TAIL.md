# 5¹⁵'s wide matching ended short: sparse blocks (2026-10-06)

**Summary.** 5¹⁵'s matching on merlin twice ended 187,166 and 187,225 requests short of 30.5
billion, both with "not an obstruction". The cause was the wide matcher's block layout, not the
field.
- **Cause:** 5¹⁵ has a tail of 78,125 cells holding 3 requests each. The new cost-balanced cut
  put that tail into blocks of its own. Each block may match only into a window of rights the size
  of its own request count. A tail block's requests had about 0.14 neighbours each inside such a
  small window, so most of them could never match there, and the exchange couldn't move them.
- **Fix:** sparse blocks now get no window. Each pass carries a slice of their cells, and the
  pass's dense blocks import those requests in their first run, into windows enlarged by exactly
  as many rights.
- **Tests:** on a small field shaped the same way, the old kernel ends about 2,270 short at every
  row budget; the new one gives a full matching, accepted by the independent verifier, at every
  budget. Fields without sparse blocks lay out and match exactly as before.

Commits: 54f9f9c (fix), with 4b434bc, 297602a, 35e86c1 and the feeder changes listed under
[Related fixes](#related-fixes-made-along-the-way).

## Timeline

| Time (CDT) | Run | What happened |
|---|---|---|
| 17:04 | `c5cb1c07` | First run on merlin. The kernel cut **216,521 one-cell blocks**, about 9 days at 3.7 s each. Cancelled. |
| 17:35 | (fix 4b434bc) | Blocks now cut by **cost**, not request count. 5¹⁵ now gets 230 blocks. |
| 18:38 | `2bfc4e57` | Passes 1 and 2 matched every block, with no leftovers. |
| 19:46 | `2bfc4e57` | Pass 3 left 187,478 requests. The solver exited "incomplete"; the agent took that for a crash and **reran the identical attempt** (lease "engine retry"). The error went unlogged. |
| 21:00 | `2bfc4e57` | Second run, same result, 187,166 short. Captured with `strace` on the kernel's stderr. |
| 21:00 | (297602a) | Agent: an incomplete result is final, never rerun. Feeder: "wide matching incomplete" moves to the next polynomial. |
| 21:03 | `0a54c28e` | Second polynomial (2,4,2,0,…,1). It ended at 22:40, **187,225 short**, the same tail. |
| 22:30–22:50 | (54f9f9c) | Diagnosis, fix and tests (this document). |
| 22:55 | `db7f496a` | Third polynomial (3,4,3,0,…,1) on the fixed kernel: 236 blocks, 4 sparse. |

## What the runs showed

`2bfc4e57`, from the kernel's stderr:

```
blocks=230 passes=3 cells_per_pass<=90503
pass 1/3 blocks [0,122) cells=84493 round-1 residual=0 pending=0
... pass 3 (the tail) ... round-1 residual=187478
31 exchange rounds and 1 rescue pass: 312 more matched, 187,166 left
```

Every leftover was in pass 3, the pass holding the tail cells. `0a54c28e`, with another
polynomial, ended 187,225 short in the same place. Two polynomials stopping at nearly the same
count meant the cause was structural, not a property of the field.

## Root cause

### The window model

The wide matcher (like `gpu_block_match_solver`) cuts the field's cells into blocks that each fit
the GPU. Each block matches its own requests into a **window**: a contiguous range of right
labels, disjoint from every other window. In these fields n = q (as many requests as labels),
so the windows tile all q labels exactly and each window is exactly its block's size m.

A request has F neighbours, spread across all q labels by the field arithmetic, so about F·m/q of
them fall in its own block's window. That number has to be comfortably above 1 for a block to
match everything.

| 5¹⁵ (F = 78,125, q = 3.05 × 10¹⁰) | Requests per cell | Requests m in a block | Neighbours in window, F·m/q |
|---|---:|---:|---:|
| Dense blocks (2F cells) | 195,311 | ~1.35 × 10⁸ | **≈ 346** |
| Tail blocks (F cells) | 3 | ~54,000 (18,000 cells × 3) | **≈ 0.14** |

At 0.14 neighbours a request, about 13% of a tail block's requests can match in its own window.
The tail has 234,375 requests and 187,478 were left over, which is about what that predicts.

### Why it showed up now

- **The tail never had blocks of its own before.** The original cut gave each block an equal
  number of requests. The tail's few requests then landed in one block alongside many dense
  cells, with a big window. 13⁹'s tail (F cells of 5 requests) matched that way.
- **But on 5¹⁵ the old cut couldn't fit the GPU.** The tail's *rows* are about 24 GB (312 KB a
  cell), far more than one block can hold, so that cut produced 216,521 one-cell blocks (17:04).
- **The cost-balanced cut (17:35) fixed that,** but it necessarily gave the tail blocks of its
  own, about 18,000 cells each, and those are the sparse blocks.
- **7¹³ is not affected.** Its thinnest cells hold 164,709 requests each.

### Why exchange couldn't fix it

Exchange imports a pending request into another block that still has free rights, along with that
request's cell row.
- **No free rights:** with n = q every dense window is exactly full after round 1, so nothing can
  move. This limit was already documented, but at production sizes it had never mattered because
  dense blocks leave nothing over.
- **Import cells:** a block could also import from at most 256 other cells (`EXTRA_MAX`), and the
  tail's requests are spread over 78,125 cells.

All 31 exchange rounds and the rescue pass together placed 312 requests.

## The fix (54f9f9c, `gpu_wide_match_solver/src/main.c`)

1. **Sparse blocks.** After the cut, a block with F·m/q < 4 is *sparse* (`SPARSE_DENSITY`,
   overridable with `--sparse-density N`, where 0 turns it off). Nothing changes unless at least
   one block is sparse and at least one is dense.
2. **Room for imports.** Dense blocks will import the sparse cells' requests, about (sparse cells
   ÷ dense blocks) cells each. The import-cell capacity (`extra_cap`, previously the fixed 256)
   grows to 256 + 1.5 × that + 16. The cut is redone with that much GPU memory reserved per block,
   at most 8 times, until it settles. If no layout fits, the kernel falls back to the plain layout,
   which behaves exactly as before.
3. **Passes.** Passes now hold consecutive *dense* blocks. Each pass also carries a slice of the
   sparse cells, proportional to its number of dense blocks, so every dense block gets about the
   same import load. A pass is closed when its dense cells plus its slice would exceed the row
   room, less 1/16 kept for carried cells.
4. **Windows.** Sparse blocks get a window of 0. Each pass's dense blocks share the pass's slice
   of sparse requests in proportion to their own m, so each window is m + share. Any spare rights
   (n < q) go to the last dense window. The windows still tile [0, q) exactly; the kernel checks
   this, and checks they fit 32-bit local indices.
5. **Setup.** Sparse blocks never run on the GPU. Their requests are written as "unmatched" in
   the payload, and all of them start out pending.
6. **Round 1 with imports.** Before a pass runs its blocks, `assign_sparse` hands the pass's
   sparse requests to its dense blocks, up to each block's share. A cell's requests go to one
   block where they fit, and cells are dealt round the blocks, so each block needs few import cells.
   - Each dense block then matches its own requests and its imports together, in its first run.
     At about 346 neighbours a request, that matches everything.
   - Matched imports are dropped from pending. Anything left goes through the existing exchange
     and rescue passes.
7. **Logging.** The layout line now reads
   `blocks=236 sparse=4 sparse_cells=69398 import_cells=746 passes=3 …`, and pass summaries end
   with `sparse_imported=N` when there are sparse blocks. The per-block line keeps its format,
   since tests and tools parse it.

**What did not change:** the device kernels, the payload format, the certificate, and the
independent verifier (`kh_verify_wide`), which checks every endpoint of the result whatever the
layout. A wrong import would be caught there, and by each block's device self-check, which covers
imports.

## Verification

**Regression test:** `blocks_sparse_tail` in `tests/check.py`. It runs 3¹⁵ with runs (2, 3279)
and (3, 1), so n = q with 2F cells of 3,280 requests and F cells of 1, under a 25.6 MB device
budget. The tail's 19 MB of rows then get blocks of their own, like 5¹⁵'s 24 GB on the 3060.

| Row budget | Committed kernel (8c34b5c) | Fixed kernel |
|---|---|---|
| unlimited (1 pass) | exit 4, 2,268 short | full matching |
| 40 MB (2 passes) | exit 4, 2,268 short | full matching |
| 25 MB (3 passes) | exit 4, 2,270 short | full matching |
| 20 MB (4 passes) | exit 4, 2,275 short | full matching |
| 15 MB (5 passes) | exit 4, 2,275 short | full matching |

`kh_verify_wide` accepted the fixed kernel's payloads: all 14,348,907 requests assigned, every
label used once.

**Unchanged behaviour elsewhere:**
- `make check` passes: the field-walk unit test, the verifier suite (29⁵, 11⁷, 7⁵, 13⁵ with
  corruptions rejected), the block fixtures, the skewed-cells test, the new test, and 60 synthetic
  graphs against the window oracle.
- The synthetic tests run with `--sparse-density 0`, since their oracle checks the plain window
  mechanism. Their summary is identical between the committed and the fixed kernel (28 exchange
  cases, 44 multi-pass, 19 rescue).
- The real-field fixtures (7⁵, 13⁵) lay out exactly as before (4 blocks, residual 0).

**At full scale (5¹⁵, run `db7f496a`, 22:56):** `blocks=236 sparse=4 sparse_cells=69398
import_cells=746 passes=3`. The other 8,727 tail cells share a block with dense cells, so their
window is large and they match normally. The result goes below once the run ends.

## Related fixes made along the way

| Commit | Where | Defect | Fix |
|---|---|---|---|
| 4b434bc | wide kernel `cut_blocks` | Equal-request blocks put the whole tail's rows in one block, so 5¹⁵ got 216,521 one-cell blocks. | Cut by device cost. New skewed-cells test. |
| 35e86c1 | agent | A retried engine failure left no record of the solver's error. | The stderr tail goes to the agent log. |
| 297602a | agent | An *incomplete* result (deterministic) was rerun as a crash: 75 minutes wasted. | "block/wide matching incomplete" fails at once. |
| 297602a | feeder | Only "block matching incomplete" moved to the next polynomial, so "wide" would have retried the same one. | Both messages are recognised (`INCOMPLETE`). |
| 54f9f9c | feeder `preferred_attempt` | With every attempt ended, the one with the most work was picked. That sent 5¹⁵ back to an older polynomial, and the feeder resubmitted the newest one (the leader returned the same failed run) on every pass. | The newest ended attempt decides. |
| 54f9f9c | feeder | Incomplete attempts lost to a defect counted toward the 3-polynomial limit. | `not_counted` attempts are skipped, as failures already were. |

**5¹⁵'s attempt record** (`pipeline.json`):
- **`not_counted`**, each with its reason: the misplaced run (P600), the 216,521-block run, and
  both sparse-tail runs.
- **The third polynomial** runs on the fixed kernel with all three attempts still available.
- **Cleanup:** duplicate entries for `0a54c28e` were removed while the feeder was stopped.

## Cost

About 3.5 hours of merlin's GPU:
- 1.2 h for `2bfc4e57`'s first, rerun attempt;
- 1.2 h for its second;
- 1.6 h for `0a54c28e`, which ran knowing it would probably fail, to confirm the cause did not
  depend on the polynomial.

## Limits and follow-ups

- **The threshold of 4 is a heuristic.** Dense blocks in production have hundreds of neighbours
  per request, and sparse tails well under 1, so the gap is wide. A field with blocks near 4 could
  still leave a small residual. It would then end incomplete (exit 4), not wrong.
- **The planner (`adapter.block_count`) doesn't model sparse handling.** It estimated 224 blocks
  for 5¹⁵, and the kernel uses 236. The estimate only sizes host memory and the wait for the GPU.
- **The general n = q limit remains:** a dense block's own leftover still has nowhere to go. It
  has never happened at production block sizes.
- **`gpu_block_match_solver`** cuts by equal requests and keeps every tail inside a big window, so
  it doesn't have this problem. It has the 216,521-block problem instead on fields like 5¹⁵, but
  it can't take F above 65,534 anyway.
