# king_hamming

`king_hamming` is a distributed implementation of the prime-power
Partition-and-Extension construction described in
[`prime_power_09_26.pdf`](docs/prime_power_09_26.pdf). For a prime power q = pʳ (r odd)
it computes the paper's DP split, finds the bipartite matching that makes the construction
valid, and stores a compact certificate that anyone can check, proving a lower bound on
M(q + 1, q).

## Status (2026-10-06)

- **88 fields proved**, from 2³ up to 7¹³ (M(7¹³ + 1, 7¹³) ≥ 27,357,428,724,361,309). Every
  field matched with the first polynomial the solver could finish (5¹⁵ needed a solver fix). The table is [`results.md`](results.md).
- **New fields:** the feeder's own frontier is off (`new_dp_fields` = 0). Since 2026-10-07 the
  hourly check starts the quickest fields not yet calculated, any p or r, two at a time
  (`campaigns/next_field.py`). Fields no matcher takes yet (2³³, 2³⁵: p = 2 past F = 65,534) get
  their DP value only.
- **7¹³ matched** on 2026-10-07 with the wide solver and verifier
  ([gpu_wide_match_solver/](gpu_wide_match_solver/README.md)), its 206 GB certificate on merlin's
  second drive ([docs/GPU_WIDE_MATCHING_PLAN.md](docs/GPU_WIDE_MATCHING_PLAN.md)).
- **The cluster:** a leader on merlin and ten household machines, each with an NVIDIA GPU,
  joined by Wi-Fi (control) and a 1 Gb/s switch (data). A dashboard on merlin shows and
  controls the campaign.

## Components

| Directory | What it is |
| --- | --- |
| [`cluster/`](cluster/README.md) | Leader (SQLite, leases, recovery, replication, retention), agents, operator CLI |
| [`dp_solver/`](dp_solver/README.md) | The exact DP: C tile kernels, distributed tiles, compact artifacts, verifier |
| [`gpu_dp_solver/`](gpu_dp_solver/README.md) | Byte-identical GPU tile kernel; it does almost all DP work now |
| [`matching_solver/`](matching_solver/README.md) | CPU matcher, KHM1 certificates, the independent verifiers |
| [`gpu_match_solver/`](gpu_match_solver/README.md) | Single-GPU matcher (`match_gpu`) |
| [`gpu_block_match_solver/`](gpu_block_match_solver/README.md) | Block matcher for fields larger than one GPU, 64-bit up to 2³⁶ (`match_gpu_blocks`) |
| [`campaigns/`](campaigns/) | The feeder that turns fields into DP and matching runs |
| [`web/`](web/README.md) | The dashboard |
| [`cuda/`](cuda/README.md) | CUDA driver loading; no CUDA toolkit is needed on workers |
| [`row_verifier/`](row_verifier/README.md) | Renders actual permutations and checks distances (small fields) |
| [`matching_solver_multi/`](matching_solver_multi/README.md) | Ownership-partitioned matching experiment |

Project adapter registration lives in `adapter_config.py`. Small frozen fixtures are in
[`examples/`](examples/).

## Documentation

**Start here**
- [docs/MATCHING_CERTIFICATE.md](docs/MATCHING_CERTIFICATE.md): what a certificate is and
  why checking it proves the bound, in plain language.
- [docs/CONTINUOUS_CAMPAIGN.md](docs/CONTINUOUS_CAMPAIGN.md): the live campaign and how to
  operate it; [cluster/README.md](cluster/README.md) for the commands.
- [docs/CLUSTER_PROGRAMMING_LESSONS.md](docs/CLUSTER_PROGRAMMING_LESSONS.md): what building
  this taught us, for the next project.

**How it works**
- Mathematics and requirements: [docs/DESIGN.md](docs/DESIGN.md), [docs/FIELD.md](docs/FIELD.md),
  [docs/TRANSITIONS.md](docs/TRANSITIONS.md), [docs/RESOURCE_MODEL.md](docs/RESOURCE_MODEL.md).
- Cluster: [cluster/DESIGN.md](cluster/DESIGN.md), [docs/PROTOCOL.md](docs/PROTOCOL.md),
  [docs/RECOVERY.md](docs/RECOVERY.md), [docs/RETENTION.md](docs/RETENTION.md).
- Distributed DP: [docs/TILES.md](docs/TILES.md), [docs/QUEUED_TILES.md](docs/QUEUED_TILES.md),
  [docs/DP_STORAGE.md](docs/DP_STORAGE.md), [docs/DP_NETWORK_LOCALITY.md](docs/DP_NETWORK_LOCALITY.md).
- GPUs and large matchings: [docs/GPU.md](docs/GPU.md),
  [docs/GPU_BLOCK_MATCHING.md](docs/GPU_BLOCK_MATCHING.md), [docs/MATCHING_13_9.md](docs/MATCHING_13_9.md).
- Dashboard: [web/DESIGN.md](web/DESIGN.md), [web/README.md](web/README.md).

**Reports and incidents**
- [docs/LOST_TILES_INCIDENT_2026-10-04.md](docs/LOST_TILES_INCIDENT_2026-10-04.md): finished
  tiles cleared on every worker upgrade, and the four-layer fix.
- [docs/TILE_SCRATCH_RAM.md](docs/TILE_SCRATCH_RAM.md): tile scratch moved to RAM, and the
  fleet's drive health.
- [docs/MACHINE_CONTRIBUTIONS.md](docs/MACHINE_CONTRIBUTIONS.md): how much each machine
  contributes, and how that was measured.
- [docs/HARDWARE_BRIEF.md](docs/HARDWARE_BRIEF.md): the workload and fleet, for hardware
  planning.
- [docs/NETWORK_OUTAGE_2026-10-02.md](docs/NETWORK_OUTAGE_2026-10-02.md): the Wi-Fi roaming
  outage.
- [docs/CLUSTER_INVENTORY.md](docs/CLUSTER_INVENTORY.md): hardware and OS facts per machine.

**History** (dated snapshots, kept as a record): [NEXT_CONVERSATION.md](NEXT_CONVERSATION.md),
[docs/NEXT.md](docs/NEXT.md), [docs/DISTRIBUTED_DESIGN.md](docs/DISTRIBUTED_DESIGN.md),
[docs/DP_CAMPAIGN.md](docs/DP_CAMPAIGN.md), [docs/CAPACITY_CAMPAIGN.md](docs/CAPACITY_CAMPAIGN.md),
[docs/PARTITIONED_MATCHING.md](docs/PARTITIONED_MATCHING.md), [docs/DP_SOLVER_CONCERNS.md](docs/DP_SOLVER_CONCERNS.md),
[docs/RECOVERY_HANDOFF.md](docs/RECOVERY_HANDOFF.md), [docs/RECOVERY_EXPERIMENT.md](docs/RECOVERY_EXPERIMENT.md),
[docs/LEADER_CONTENTION_AND_DP_RECONSTRUCTION_PLAN.md](docs/LEADER_CONTENTION_AND_DP_RECONSTRUCTION_PLAN.md).

## Build and test

From the repository root:

```sh
make -C king_hamming/dp_solver check
make -C king_hamming/matching_solver check
make -C king_hamming/cluster check
make -C king_hamming/gpu_match_solver check   # needs a CUDA GPU
make -C king_hamming/gpu_dp_solver check      # needs a CUDA GPU
make -C king_hamming/gpu_block_match_solver check   # needs a CUDA GPU
make -C king_hamming/web check
```
