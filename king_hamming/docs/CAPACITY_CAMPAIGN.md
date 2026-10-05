# Capacity-first continuous campaign

> **Status (2026-10-04).** Built but not the policy running: the live campaign's `pipeline.json` has no `policy` field, so the feeder uses the default (legacy) policy.

The separate entry point is `campaigns/capacity_campaign.py`. It reuses the
retained DP/collection/retry machinery rather than creating a second cluster
implementation. Old pipelines without a `policy` field keep the existing policy.

Single-host execution and automatic minimum-owner fallback are implemented.
The partitioned adapter uses ordinary scheduler reservations, lease-fenced
participants, enforced native memory limits, and durable all-owner checkpoints.
DP continues even when matching admission is blocked. Deployment observations
are recorded separately below; implementation alone is not proof of rollout.

## Selection rules

1. Preserve configured hard limits. The default matching ceiling remains 6 GiB
   per machine and the default field ceiling remains 100,000,000. The new policy
   does not treat failing a field/edge ceiling as permission to bypass it.
2. Consider healthy compute nodes with known RAM and CPU allocations, including
   busy nodes. Temporary occupancy does not justify distributing a fitting job.
   Subtract 2 GiB operating-system headroom and apply the configured per-machine
   ceiling. Unknown RAM is not treated as unlimited capacity.
3. Use the existing `match` native single-host solver if it fits anywhere. Its
   estimate mirrors the C solver's field/search peaks, includes verification,
   and adds a 25% margin. Try fewer threads before splitting across machines.
4. Otherwise try groups of 2, 3, ... up to `matching_workers` (at most 16).
   Every participant must fit its own peak; combined RAM alone is insufficient.
   Prefer larger eligible machines within each candidate group. Include owned
   arrays, communication buffers, discovery bitmap, thread stacks, verification
   on the coordinator, and the same safety margin.
5. Submit the first feasible group as `match_partitioned`. The scheduler reserves
   each participant's own budget and rechecks measured capacity at dispatch.
   Native owners enforce their address-space ceilings. The conservative envelope
   is not a claim of measured performance at every large field size.

The existing scheduler reserves CPU/memory for single-host jobs at dispatch.
Capacity-policy submissions explicitly require known RAM/CPU at dispatch too;
legacy jobs retain the old unknown-capacity compatibility behavior. Deploy the
updated adapter and resource contract before starting this new feeder.
Reconciliation requires the leader's `known-capacity-admission-v1` and
`partitioned-matching-v1` capabilities
and refuses an older leader before making changes. Read-only preview remains
available against old leaders.
Names shown by the planner are advisory: node availability may change before
dispatch, and the scheduler may choose another fitting host. Partitioned names
are planning hints; the actual group is allocated through the scheduler.

Failures retry the same pinned polynomial within the existing retry budget;
solver crashes do not cause an automatic change of algorithm. Completed results
are collected before new admission checks, even if hosts disappear. Durable DP
artifacts are retained when matching cannot proceed. Blocked partitioned fields
do not count as matching backpressure that would stop useful DP production.
Existing exact DP/polynomial runs are reattached if an enqueue reply was lost
before the feeder saved it, including when a later plan chooses different thread
or memory settings.

## Read-only preview against the current campaign

From the repository root:

```sh
python3 campaigns/capacity_campaign.py \
  --state cluster/deployments/continuous-campaign plan
```

This reads current saved inputs/settings and live inventory. It does not change
the pipeline, enqueue anything, stop workers, or raise limits. On 2026-09-30 the
unmatched saved fields were 2^27, 2^29 and 3^17, all blocked by the current
100,000,000 field ceiling. Capacity fallback cannot bypass that separate setting.

## Initialize a separate retained deployment

After a fresh, non-overlapping cluster deployment has been provisioned, use its
directory (containing `manifest.json` and no existing `pipeline.json`):

```sh
python3 campaigns/capacity_campaign.py \
  --state cluster/deployments/capacity-campaign init
python3 campaigns/capacity_campaign.py \
  --state cluster/deployments/capacity-campaign once
python3 campaigns/capacity_campaign.py \
  --state cluster/deployments/capacity-campaign run --interval 120
```

These are setup instructions, **not evidence that this deployment currently
exists**. Do not start a second agent pool on CPUs already assigned to the live
campaign. A switchover should retain/import completed artifacts and quiesce the
old deployment before transferring the same machines.

Initialization records `policy: capacity`, `memory_margin_percent: 25`, and
`partitioned_batch: 65536`. Both extra settings have initialization flags. The
normal feeder command also reads this persisted policy, so a retained feeder
restart cannot silently revert it. Initialization refuses to overwrite existing
pipeline state; the capacity entry point refuses `once`/`run` on a legacy
pipeline. `plan` is intentionally allowed on legacy state for comparison.

## Retained deployment adoption

To reuse the existing pool without duplicating work or overlapping CPUs: stop
dispatch, wait for every running/stopping row to quiesce, upgrade workers, then:

```sh
python3 campaigns/capacity_campaign.py --state cluster/deployments/continuous-campaign adopt
python3 dp_solver/launch_dp.py --state cluster/deployments/continuous-campaign resume
```

`adopt` checks upgraded capabilities, holds the feeder and database write locks,
refuses active work or running dispatch, and preserves all results, attempts,
checkpoints and limits. It retains `pipeline.before-capacity.json`. Existing
queued attempts keep their original solver; new attempts use capacity routing.
The normal retained feeder reads the persisted policy on each reconciliation.

## Validation and remaining scale work

```sh
make -C matching_solver all
make -C matching_solver_multi all
python3 cluster/tests/test_capacity_campaign.py
python3 cluster/tests/test_continuous_campaign.py --run
python3 matching_solver_multi/tests/check.py
python3 matching_solver_multi/tests/test_runtime.py
python3 matching_solver_multi/tests/test_cluster.py
```

The new tests cover C/Python admission parity, busy and unknown-capacity hosts,
thread-count reduction, smallest feasible groups, heterogeneous per-host limits,
preserved hard limits, idempotent submission of both solvers, and guarded adoption.
Managed-runtime tests exercise real three-agent leases, pause/restore, a killed
participant, stale lease rejection, corrupt images and interrupted export. Native
restore also validates saved edges against the reconstructed field. Certificates
are independently verified before publication.

Remaining scale work: measure larger-field memory and checkpoint costs before
raising limits; accelerate streaming certificate packing/verification; support
changing owner count when restoring a checkpoint; and reconsider queued group
sizes when the hardware inventory changes. Current checkpoints can move between
hosts but retain the original owner count. This is a memory-capacity fallback,
not a promise that distributed matching is faster than the single-host solver.
