# Full-scale matching readiness

The retained `match-overnight` campaign is stopped. Of 44 admitted saved DP
fields, 41 have complete certificates; 2^25, 7^9, and 5^11 retain unfinished
queue records. The matching agents are separate from the DP campaign. When the
campaign is active, its watcher archives each completed KHM1, retries the next
primitive polynomial only after a verified Hall obstruction, admits newly
collected DP fields within the current limits, and reattaches dead
campaign-owned processes. The per-job address-space cap is 2 GiB; admission
mirrors the native C kernel's peak-memory estimate. The status table is
`../cluster/deployments/match-overnight/STATUS.md`.

The three remaining saved fields exceed that cap:

| Field | q | Implicit edges | Native estimated peak |
| --- | ---: | ---: | ---: |
| 3^17 | 129,140,163 | 847,288,609,443 | 4.44 GiB |
| 2^27 | 134,217,728 | 1,099,511,627,776 | 4.61 GiB |
| 2^29 | 536,870,912 | 8,796,093,022,208 | 18.20 GiB |

`3^17` and `2^27` could use a larger per-host allocation if core affinity,
concurrent DP load, and physical RAM leave enough headroom. `2^29` requires
sharding or external-memory state. The multi-node production engine now keeps
all mathematical work in C. Python only establishes leased process streams. One
native coordinator owns matching state, BFS, augmentation, KHS1, and KHM1; one
native field process per node scans across that node's cores. Each shard now
keeps replicated matching and distance state, consumes generated labels locally,
and returns only BFS discoveries or complete path proposals. The coordinator
merges nonconflicting paths and broadcasts committed deltas. No edge-label stream
crosses machines.

For the unfinished household matching jobs, the reduced engine's conservative
per-process admission estimates are:

| Field | Requests | Coordinator | Each native shard |
| --- | ---: | ---: | ---: |
| 2^25 | 33,554,432 | 1.82 GiB | 1.63 GiB |
| 7^9 | 40,353,607 | 2.18 GiB | 1.95 GiB |
| 5^11 | 48,828,125 | 2.62 GiB | 2.35 GiB |

Only 2^25 fits both sides of the present 2 GiB limit, and only narrowly. These
are allocation bounds, not measured resident sets; measure resident memory on a
larger calibration before changing the stopped campaign.

Before raising the frontier, finish these implementation items:

1. Make checkpoints possible within a very long native matching phase. Current
   snapshots occur only at phase boundaries, so a single phase can exceed the
   configured 30-minute interval.
2. Measure per-node resident memory and network traffic on a larger real-cluster
   calibration. The current replicas admit 2^25 under the 2 GiB estimate, but
   only narrowly; 7^9 and 5^11 require a larger cap or partitioned state. New
   runs automatically retain per-coordinator and per-shard CPU time and peak RSS
   in the leader database for this comparison.
3. Add a local node supervisor that detects a hung but still-live agent, restarts
   it, and requests at most one host reboot if the restart does not restore
   heartbeat. The current watcher restarts only processes that have exited.
4. Test restart and certificate retrieval after a real agent replacement,
   including reuse of the registered storage port, and run a multi-hour failure
   injection with DP workers concurrently active.

The saved 13^5 benchmark progressed through three architectures on the same
local topology: 115.7 seconds with Python interpreting every edge, 23.81 seconds
with a C coordinator receiving every label, and 2.02 seconds after moving BFS
reduction and path proposals into the shards. Four local shards with two native
threads each completed in 1.83 seconds. Both reduced runs independently verified
all 371,293 assignments. This is a 57x improvement over the Python path.

A private two-machine 13^5 run on `.107` and `.108`, with two native threads per
node, completed in 10.74 seconds including a deliberate partner failure and
recovery. The replacement lease restored 368,414 committed assignments from
two checkpoint replicas, and the final KHM1 independently verified. This
validates the reduced wire protocol and recovery path on real hosts, but it does
not authorize `2^25`: that field still has a narrow 2 GiB margin and no
intra-phase checkpoint for its potentially very long first phase.

Do not describe either native engine as covering the full uint32 field range.
Keep the retained campaign stopped while the remaining readiness work is
implemented and tested.
