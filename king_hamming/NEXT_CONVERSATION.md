# King Hamming matching handoff

Snapshot: 2026-09-28, America/Chicago.

## Suggested opening prompt

Continue the King Hamming matching work from this handoff. First inspect the
live matching campaign without changing it, then preserve the currently running
jobs until they reach terminal states. After that, pursue the production
milestones below. Do not interfere with the separate DP campaign.

## Live matching campaign

- Leader: `http://192.168.4.151:8051`
- State: running
- Deployment: retained `match-overnight` campaign using the older single-host
  matching bundle, with agents `match-101` through `match-108`, each assigned
  CPUs `0,1`.
- The campaign was resumed exactly as deployed. No allocation or queue changes
  were made.
- Status command:

  ```sh
  python3 king_hamming/cluster/kh.py \
      --leader http://192.168.4.151:8051 status
  ```

Current unfinished work:

| Field | Run ID | State at snapshot | Placement | Durable state |
| --- | --- | --- | --- | --- |
| 5^11 | `8a0c4a80-e6d9-45e5-ad0c-2889bcb2200f` | running, 48,828,122 / 48,828,125 | `match-105` | checkpoint at 48,828,122, eight live copies |
| 2^25 | `3a3ebe8a-4321-4dbc-8155-7acd79d60c99` | running, 33,548,714 / 33,554,432 | `match-106` | no retained checkpoint |

`7^9` completed after the campaign resumed:

- Run: `169892a5-c9e4-4a63-853e-06affb14e505`
- Restored at 40,353,602 and completed 40,353,607 / 40,353,607.
- Artifact SHA-256:
  `4fb3a3d14455075884eec1eda136f9302702abe7c26541dd79f8b08dc6ca9c27`

The two running jobs currently show `heartbeat-missing`. This deployment emits
solver progress at native phase boundaries, so that label alone does not prove a
process is stalled. Inspect the exact agent/kernel process and CPU activity
before stopping or restarting anything. In particular, do not casually discard
the checkpoint-free 2^25 attempt.

The separate DP campaign is out of scope and must not be stopped, redeployed, or
otherwise modified while handling matching.

## What the current source tree now contains

The new `match_distributed` implementation keeps mathematical work in C:

- Native shards build fields and perform BFS scans and augmenting-path search.
- Edge labels never cross machines.
- The C coordinator merges discoveries/path proposals, owns canonical matching
  state, writes KHS1 checkpoints, and emits KHM1 certificates.
- Python only handles leases and process streams.
- Recovery can restore a canonical checkpoint into a different shard count.
- Native solver/coordinator/shard processes report user+system CPU time and peak
  RSS. New-bundle cluster runs retain these in SQLite `resource_usage`, keyed by
  lease attempt and shard, and `kh.py status` displays them.

Important files:

- `matching_solver/src/distributed.c`
- `matching_solver/src/distributed_worker.c`
- `matching_solver/src/resource.c`
- `matching_solver/native_coordinator.py`
- `cluster/leader.py`
- `cluster/agent.py`
- `matching_solver/FULL_SCALE.md`
- `matching_solver/DISTRIBUTED.md`

Verified performance for saved 13^5 (371,293 requests):

- Original Python algorithm: 115.7 seconds.
- C coordinator receiving every edge label: 23.81 seconds.
- Reduced native protocol, two local shards × four threads: 2.02 seconds.
- Four local shards × two threads: 1.83 seconds.
- Private two-host recovery test: 10.74 seconds including deliberate partner
  failure; restored 368,414 assignments from two replicas and independently
  verified the final KHM1.

Both suites passed after the resource-accounting work:

```sh
make -C king_hamming/matching_solver check
make -C king_hamming/cluster check
```

The live `match-overnight` agents have not been upgraded to this new bundle, so
their current jobs will not produce the new per-shard resource rows.

## Recommended next milestone

First let 5^11 and 2^25 finish and verify/archive their KHM1 artifacts. Do not
redeploy their agents while either attempt is active.

After they are terminal, make the reduced distributed engine production-ready
in this order:

1. Add intra-phase checkpointing. Current distributed checkpoints occur only at
   completed augmentation barriers; a very long first phase can still lose
   hours of work.
2. Add heterogeneous group resources so Merlin can contribute 12 cores while
   smaller agents contribute their actual allocations. The current distributed
   job has one `threads` value for every shard.
3. Raise and test the generic 8-node reservation ceiling if the intended group
   is Merlin plus all eight household workers (nine nodes). The native protocol
   itself supports more, but the generic scheduler and adapter currently admit
   only 2–8 nodes.
4. Add per-run queue controls: cancel, pause/resume, and reprioritize one run
   without stopping the entire campaign.
5. Start a fresh isolated campaign/port with the new bundle, run a medium
   calibration, and inspect the retained per-shard CPU/peak-RSS rows before
   admitting another giant field.

Current conservative replicated-state estimates under the new engine:

| Field | Coordinator | Each shard |
| --- | ---: | ---: |
| 2^25 | 1.82 GiB | 1.63 GiB |
| 7^9 | 2.18 GiB | 1.95 GiB |
| 5^11 | 2.62 GiB | 2.35 GiB |

Only 2^25 narrowly fits both sides of the present 2 GiB cap. Do not infer that
7^9 or 5^11 is safe under the new replicated distributed path without changing
the memory cap or partitioning state.

## Operational guardrails

- Prefer read-only status and exact process/CPU inspection before intervention.
- Preserve retained checkpoints and certificates.
- Do not overwrite the existing `match-overnight` state directory.
- Do not deploy new binaries into agents that still own an active old-bundle
  matching attempt.
- Do not use the old campaign as the first experiment for scheduler-limit or
  heterogeneous-thread changes; use an isolated leader and fresh deployment.
