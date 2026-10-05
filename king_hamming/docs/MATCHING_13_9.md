# Matching 13^9: why it is blocked and what it would take

Status: **done.** 13^9 was matched, independently verified and archived on 2026-10-04
(run `cd77bd61`, all 10,604,499,373 requests, 169 blocks, no exchange rounds), proving
M(13⁹ + 1, 13⁹) ≥ 1,038,436,628,063,094. The write-storm fixes in section 8 were deployed in
the later worker rollouts. Sections 1–7 are the original analysis (written the same day from the code and the live leader database), kept
as the record of why. Re-verify numbers before acting; the commands are in section 6.

## 1. The question

The dashboard's Matching tab says 13^9 "needs 338 GiB of GPU memory; largest GPU holds
…". Is that true, and is there a way around it?

## 2. Short answer

The figure is correct for matching the whole field on one GPU (`match_gpu`), and it is
about 12 times the combined memory of every GPU in the fleet. But **GPU memory is not the
real blocker**. A block-by-block GPU matcher (`match_gpu_blocks`) already exists and needs
only a few GiB of device memory per block. 13^9 is still refused, for two other reasons:

1. **32-bit limits.** The matching kernels index requests and field elements with
   `uint32_t`. 13^9 has q = 10,604,499,373, which is 2.47 × 2^32.
2. **Host RAM.** The block kernel keeps the full field table on the host, about
   `4q + 2n` = 59 GiB. The largest machine has 38.9 GiB.

The dashboard note is therefore misleading: it attaches a GPU-memory figure to a field
whose admission status is `field limit`.

## 3. The facts

All from the live campaign (`cluster/deployments/continuous-campaign`).

| Quantity | Value | Source |
| --- | --- | --- |
| Field | 13^9, DP complete, `matching_admission = field limit` | `pipeline.json` |
| q = requests n | 10,604,499,373 (2.47 × 2^32) | `pipeline.json` |
| Edges | 302,875,106,592,253 (3.0 × 10^14) | `pipeline.json` |
| Whole-field GPU memory | 338.3 GiB = 8q + 24n + 18(n/8+1) + 16 MiB | `gpu_match_solver/adapter.py:device_bytes` |
| Total memory of all GPUs | about 29 GiB (8 × 1.95 GiB P600, 5.7 GiB RTX 3060, 3.9 GiB 1050 Ti, 3.6 GiB T1000) | `nodes.gpus_json` |
| Block kernel host memory | 59.3 GiB = 4q + 2n | `gpu_block_match_solver/adapter.py:host_bytes`, README |
| Host RAM | dp-151 and merlin 38.9 GiB; dp-152 14.8 GiB; dp-156 7.4 GiB; the other eight workers 15.5 GiB | `nodes.memory_bytes`, `/proc/meminfo` |
| Output size | about 2n bytes = 19.8 GiB | choice array, 2 bytes per request |
| CPU matching limit | `max_field_elements` = 100,000,000 (feeder setting) | `pipeline.json` settings |

It is the only DP-complete field not yet matched; no other field is affected.

### Where the code refuses it

- `campaigns/gpu_policy.py:plan` returns `None` at once when `dp["q"] > 2**32 - 1` or
  `request_count(dp) >= 2**32 - 1`, so neither the single-GPU nor the block plan is tried.
- `gpu_block_match_solver/src/main.c` has the same bound (`stripes * f > UINT32_MAX - 1`,
  `uint32_t` request, parent and root indices), and the README states the limit
  `q <= 2^32 - 1`.
- Even without the 32-bit limit, no host has 59 GiB: `gpu_policy.block_plan` skips any node
  whose RAM minus `HOST_RESERVE_BYTES` is below `block_adapter.host_bytes`.
- The CPU path is capped by `max_field_elements` (1e8, versus q = 1.06e10).

## 4. Ways around it

| Option | What it removes | Cost and unknowns |
| --- | --- | --- |
| **Prefix-only host table** (stage 2 of `docs/GPU_BLOCK_MATCHING.md`): store only the field rows a block uses, via a `kh_build_field_prefix(..., cell_limit)` | The 4q host table. Host memory falls to about 2n ≈ 20 GiB plus the used rows, which fits dp-151 and merlin. | Not written: no `kh_build_field_prefix` exists anywhere in the repo. Does not help the 32-bit limit by itself. |
| **64-bit global indexing** in the block matcher: keep each block's local indices 32-bit, make request IDs, parent/root pointers and the cross-block exchange 64-bit | The 2^32 limit. | The larger job: host exchange code, packed output format, both verifiers (native and Python), and the admission checks. Block device cost per request is unchanged but the host-side import/exchange tables grow. |
| **Distributed CPU matching** (`matching_workers` program) with a raised `max_field_elements` | Neither GPU nor single-host limits. | Not evaluated. Memory per element and the time for 3 × 10^14 edges are unknown. The fleet's total RAM is roughly 185 GiB (workers only), which may not be enough for q = 1.06e10. Do not assume it works. |
| **Do nothing** | n/a | 13^9 stays DP-complete and unmatched. The rest of the campaign is unaffected. |

Both of the first two are needed together for the GPU route: prefix-only for RAM, and
64-bit indexing for the 2^32 bound.

## 5. Plan

Do these in order, and stop at each gate. Ask Zooey before any run that holds a GPU or
a node for minutes.

0. **Decide it is worth it.** The payoff is a single field's result. Both engineering
   steps below are multi-day work and would hold dp-151 or merlin for the matching run.
1. **Fix the dashboard note (small, safe).** In `web/static/matching.js` (`gpuNote`) and
   `web/feeder.py`, say what actually blocks a field: "q exceeds 2^32−1" or
   "needs N GiB of host RAM; largest machine has M GiB", instead of a GPU-memory
   figure for a `field limit` field. Add a test in `web/tests`.
2. **Estimate before building.** On 11^9 (q = 2.36e9, fits today), measure the block
   run's time, host peak and block count. Extrapolate to 13^9 (q × 4.5, edges × ?). If
   the extrapolated run is many hours, stop here.
3. **Stage 2: prefix-only builder.** Add `kh_build_field_prefix` and
   `host_bytes_block`; verify bit-identical results against the current block solver on
   17^7, 2^29 and 19^7 (the stage-2 fields named in the design doc).
4. **64-bit indexing.** Extend the block kernel's global index types and the exchange
   protocol; keep block-local indices 32-bit. Extend `native_memory`, the KHM1 payload
   header and both verifiers. Test with a synthetic field just above 2^32 and with
   differently sized blocks; check `make -C king_hamming/gpu_block_match_solver check`.
5. **Admission.** Relax the `2**32 - 1` checks in `campaigns/gpu_policy.py` and the
   adapters; make `block_plan` admit only hosts with RAM for the prefix-only table, so
   only dp-151 and merlin qualify.
6. **Run 13^9** on dp-151 once, with the GPU held for the whole run (DP tiles on that
   node will fall back to CPU meanwhile), then verify with the streaming verifier.

## 6. How to re-verify the numbers

```sh
# field record and limits
python3 - <<'EOF'
import json
p = json.load(open("cluster/deployments/continuous-campaign/pipeline.json"))
print({k: p["fields"]["13^9"][k] for k in ("q", "requests", "edges", "matching_admission")})
print(p["settings"]["max_field_elements"])
EOF
# GPU and host memory per node
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("file:cluster/deployments/continuous-campaign/leader.sqlite?mode=ro", uri=True)
for r in c.execute("select node_name, memory_bytes, gpus_json from nodes"): print(r)
EOF
grep -n "2\*\*32" campaigns/gpu_policy.py
grep -n "q <= 2^32" gpu_block_match_solver/README.md
grep -n "MAX_Q" gpu_block_match_solver/adapter.py             # 2**36 - 1 after section 8
```

## 7. Related

- `docs/GPU_BLOCK_MATCHING.md` — block design, memory model, stages (read the code first;
  stage 2 there is the prefix-only builder).
- `gpu_block_match_solver/README.md` — current limits and results.
- `campaigns/gpu_policy.py` — admission routing between `match_gpu` and `match_gpu_blocks`.

## 8. What was built (2026-10-04)

The GPU route, both steps: rows of used cells only, and 64-bit labels and request counts.
13^9 is now planned as `match_gpu_blocks` on dp-151 (merlin), 69 blocks on its RTX 3060.

| Piece | Where | What changed |
| --- | --- | --- |
| Used-rows field builder | `gpu_block_match_solver/src/field_prefix.c` | 64-bit field arithmetic (digit vectors, no packed 32-bit values) and a two-pass, multi-threaded build that stores only cells `0 .. a_max·F−1`. Labels are 32-bit words; above 2^32 each row also has `(q−1) >> 32` breakpoints, so 4 bytes per label still suffice. Spot-checks 2,000 labels against independently computed powers. |
| Block kernel | `src/main.c`, `src/kernels.cu` | Labels, rights, windows and request counts are 64-bit; indices inside a block stay 32-bit. `--choice-file` keeps the 2-byte-per-request choices in a mapped scratch file. Progress inside every stage. The final payload write is multi-threaded. |
| Native verifier | `matching_solver/src/verify_khm1.c` | Accepts q up to 2^36 with the same low-word-plus-breakpoint rows. Still independent of the solver code. |
| Admission | `campaigns/gpu_policy.py`, the three adapters | Fields above 2^32 go straight to block planning; `MAX_Q = 2^36 − 1` for block mode only (CPU and single-GPU matching keep 2^32 − 1). `host_bytes()` mirrors the kernel. `gpu_policy.blocker()` names the binding limit for the dashboard. |
| Bridge and agent | `cluster_solver.py`, `cluster/agent.py` | The bridge sends a stage record (GPU wait, field, blocks, exchange, write, publish) and the live unmatched-request burndown as the progress message; the agent keeps it while it verifies. |
| Dashboard | `web/matching.py`, `web/static/matching.js` | Stage list with per-stage progress, time left and a stacked timeline; live linear burndown for block runs; a stage that keeps reporting is not shown as stalled. |

### Memory for 13^9

| | Before | Now |
| --- | ---: | ---: |
| Kernel host memory | 59 GiB (full 4q table + choices) | 17.7 GiB (rows 15.2 GiB, choices on disk) |
| Native verifier | refused (q > 2^32) | 16.5 GiB |
| Largest host (merlin) | 38.9 GiB | 38.9 GiB |

Scratch disk on the run's node: about 20 GiB of choices, 20 GiB of raw payload and the
20 GiB certificate, plus the blob-store copy.

### Measurements

- **13^9 used-rows build** (`tests/bench_field 13 9 5 8`, 8 low-priority threads on a busy
  merlin): 394 s, 15.2 GiB peak, every row ascending, spot check passed.
- **11^9 rehearsal** (q = 2.36e9, 16 blocks on the 3060, through the new bridge):
  GPU wait 3 s, field 88 s, blocks 147 s (all 16 complete in round 1, no exchange), write
  180 s (was 1,237 s single-threaded), publish 14 s. Independent verification of the same
  field took 1,340 s (single-threaded) and accepted it.
- **Estimate for 13^9** (not measured): field about 7 min; blocks 15–35 min; write 15–20 min;
  publish 1–2 min; verification about 1.5–2 h. About 2.5–3 hours in total, most of it the
  verifier.

### Tests

- `make -C gpu_block_match_solver check`: the builder against `dp_solver/src/field.c` on 18
  small fields (also with narrow label words, which exercise the breakpoints); fixtures with
  narrow words, the choice file and multi-segment parallel writes; 60 synthetic graphs, a
  third of them with narrow words, under the per-block exact-matching oracle.
- `matching_solver/tests/check_native_verify.py`: native (32-bit and narrow words) agrees with
  the Python verifier on good certificates and on corruptions.
- `gpu_block_match_solver/tests/check_cluster.py`: a real leader and GPU agent, including the
  stage record. `cluster/tests` (270 tests), `web/tests` (102).

No small field above 2^32 exists (the smallest are 3^21 and 13^9 itself), so the 64-bit
paths are tested by narrowing the label words on small fields, plus the 13^9 row build.

### Rollout

Leader (adapter limits at enqueue), workers (kernel, verifier, bridge, agent) and feeder
(admission) together: drain, wait until idle, `upgrade-leader`, `upgrade-workers` (restarts
the feeder), resume. The feeder then queues 13^9 for dp-151. While it runs, merlin's GPU is
held (its DP tiles use the CPU), 17.7 GiB of its memory is reserved, and an incomplete
block run (residual above about n/1000) costs one of the field's three attempts.

### First run (2026-10-04): a write storm stalled the leader

Run `cd77bd61` attempt 1 lost its lease 19.5 min in, together with every other lease in the
fleet (all 11 nodes, 01:58). At the end of its field build the kernel filled the 20 GB scratch
choice file with 0xFF. That flooded merlin's disk with dirty pages; the leader's SQLite
fsyncs, on the same disk, waited behind them for over a minute; no lease could be renewed, and
the leader expired them all once it recovered. Attempt 2 repeated the fill (same code) with a
dirty-page cap in place and stalled the leader for about 100 s, costing one tile lease.

Fixes:

- **Live now:** `vm.dirty_background_bytes=128 MiB`, `vm.dirty_bytes=512 MiB` on merlin (runtime
  only; reverts at reboot). The leader forgives its own stalls: after more than 15 s without a
  commit (`leader.STALL_SECONDS`), running leases get one full lease to renew in before expiry
  runs (`recovery.forgive_stall`; test in `test_recovery.py`). Deployed by a live leader restart.
- **In the code, since deployed with the worker rollouts of 2026-10-04:** choices are stored XOR 0xFFFF, so a
  fresh sparse file already means "unmatched" and nothing is filled; each block's choices are
  written through (`msync`) after the block; the payload write `fdatasync`s every segment
  (~120 MB); `publish()` and `store_blob()` write through every 256 MiB.

