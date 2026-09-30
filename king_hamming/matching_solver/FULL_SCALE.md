# Full-scale matching readiness

The retained unified campaign at `192.168.4.151:8061` uses this compact,
checkpoint-compatible bundle. Its conservative 6 GiB matching admission and
100,000,000-element field gate remain in force pending physical calibration.
See [`../docs/CONTINUOUS_CAMPAIGN.md`](../docs/CONTINUOUS_CAMPAIGN.md) for live
status and control commands.

## Compact replicated design

The reduced distributed engine still replicates the hot matching state on each
shard, avoiding a network lookup for every implicit edge. Its deployed memory
layout is:

- the unused coordinator edge-offset table is gone;
- choices use 16 bits after validating `f <= 65535`;
- left-claim maps use one bit per vertex;
- each shard's path reply is bounded to 262,144 assignments;
- coordinator checkpoint restore applies assignments in bounded chunks;
- BFS replies are deduplicated directly into the coordinator frontier instead
  of retaining a full reply array for every shard;
- shard discovery lists allocate fixed chunks only as discoveries occur rather
  than reserving a full-graph array at every level.

The matching and checkpoint formats remain unchanged. A path deeper than the
bounded assignment reply is rejected as a resource failure, not reported as a
mathematical obstruction.

Conservative admission estimates for nine shards and sixteen
threads per shard are:

| Field | Implicit edges | Coordinator | Each shard |
| --- | ---: | ---: | ---: |
| 2^25 | 137,438,953,472 | 0.81 GiB | 0.78 GiB |
| 7^9 | 96,889,010,407 | 0.95 GiB | 0.92 GiB |
| 5^11 | 152,587,890,625 | 1.13 GiB | 1.10 GiB |
| 3^17 | 847,288,609,443 | 2.86 GiB | 2.80 GiB |
| 2^29 | 8,796,093,022,208 | 11.69 GiB | 11.41 GiB |

These are allocation bounds, not a deployment authorization. At `2^29`, a
small host has only modest headroom above an 11.41 GiB shard, while Merlin must
hold its local shard and coordinator together. Measure a physical calibration
and include the agent, Python bridge, kernel, allocator, and operating-system
overhead before raising the live field or memory limits.

## Verification completed

- Full matching and cluster suites pass.
- Native distributed matching, portable KHS1 resume, group recovery, and KHM1
  verification pass with the compact layout.
- A focused 13^5 run exercised depth-two BFS, bounded restore-compatible state,
  and independently valid full matching output.
- The feeder's source admission formula is regression-tested for `2^29` below
  12 GiB on both coordinator and shard sides.

## Safe progression

1. Let the deployed `5^11` attempt finish and retain coordinator and shard peak RSS.
2. Run `3^17` first in an isolated calibration campaign with a 4–6 GiB cap and
   verify its KHM1, recovery path,
   per-thread affinity, wall time, and combined coordinator-plus-local-shard RSS.
3. Calibrate the compact engine on a synthetic allocation or intermediate field
   before granting a 12–13 GiB per-process cap for `2^29`.
4. Admit `2^29` only with a disk-space check, frequent portable checkpoints,
   and enough local memory headroom on every selected host.

If the physical peak does not fit reliably, the next architecture is ownership
partitioning of `left`, `right`, distance, and frontier state with batched
cross-node exchanges. That removes replication but is a separate distributed
algorithm and should not be mixed into the compact-layout deployment.
