# king_hamming

`king_hamming` is a distributed implementation of the prime-power
Partition-and-Extension construction described in
[`prime_power_09_26.pdf`](docs/prime_power_09_26.pdf).

Small frozen KHD1 fixtures used for compatibility tests live in [`examples/`](./examples/).
The superseded proof-of-concept implementation has been removed; production DP,
matching, verification, and printing live in their dedicated directories.

The distributed control plane is in
[`cluster/`](./cluster/). It contains the leader, affinity-aware agent, operator CLI,
checkpointable demonstration solver, content-addressed worker storage,
replication, standalone verification, and an end-to-end integration test.

The complete DP implementation is in [`dp_solver/`](./dp_solver/): C kernels,
resource estimates, checkpoint/resume, independent verification, compact artifacts,
and Python integration with the generic cluster. Project adapter registration
lives in `adapter_config.py`. [`matching_solver/`](./matching_solver/) now has a local C exact matcher with pinned parallel search, compact KHM1 certificates,
independent verification, hydration, and a registered cluster adapter for pinned
field attempts with replicated phase checkpoints and final certificates.

GPU acceleration ([`docs/GPU.md`](docs/GPU.md)): [`gpu_match_solver/`](./gpu_match_solver/)
provides the exact single-GPU `match_gpu` program (seconds instead of hours, KHM1
certificates verified as usual), and [`gpu_dp_solver/`](./gpu_dp_solver/) a
byte-identical GPU drop-in for `kh_dp_tile` that tile leases use opportunistically.
Both load the NVIDIA driver at run time through [`cuda/`](./cuda/); no CUDA toolkit
is needed on workers.

The design documents at the project root are:

- [`docs/CLUSTER_INVENTORY.md`](docs/CLUSTER_INVENTORY.md): current hardware, OS,
  storage, NUMA, watchdog, and toolchain facts for `.101`-`.108` and `.151`.
- [`docs/DISTRIBUTED_DESIGN.md`](docs/DISTRIBUTED_DESIGN.md): proposed architecture for
  distributed DP and matching, one resident field per machine, checkpointing,
  failure recovery, scheduling, and implementation order.
- [`docs/RESOURCE_MODEL.md`](docs/RESOURCE_MODEL.md): integer widths, standard DP tile,
  per-machine memory admission, runtime ordering, and progress thresholds.
- [`docs/DESIGN.md`](docs/DESIGN.md): accumulated mathematical, artifact, operational,
  and cluster requirements from the brainstorming process.
- [`docs/MATCHING_CERTIFICATE.md`](docs/MATCHING_CERTIFICATE.md): plain-language
  explanation of the `.khmatch` certificate and why checking it proves the bound.

The control and storage protocol supports both a demonstration solver and the
exact C tiled DP. Exact identical-cost
transition reduction is implemented with preserved ties and checkpoints; see
[`dp_solver/TRANSITIONS.md`](docs/TRANSITIONS.md) and
[`dp_solver/NEXT.md`](docs/NEXT.md). The shared production field builder and local matching engine are implemented;
matching checkpoint replication is implemented; the production multi-machine
matching path now keeps algorithmic work in C and has passed fenced recovery and
exact certificate verification. Long-tile progress and responsive control polling
are implemented; see [`cluster/PROTOCOL.md`](docs/PROTOCOL.md).

To build and test the production implementations from the repository root:

```sh
make -C king_hamming/dp_solver check
make -C king_hamming/matching_solver check
make -C king_hamming/cluster check
make -C king_hamming/gpu_match_solver check   # needs a CUDA GPU
make -C king_hamming/gpu_dp_solver check      # needs a CUDA GPU
```

Replicated native DP checkpoints and cross-worker recovery are now implemented
and tested on the household machines. [`cluster/RECOVERY.md`](docs/RECOVERY.md)
describes the protocol and [`cluster/RECOVERY_EXPERIMENT.md`](docs/RECOVERY_EXPERIMENT.md)
records exact full-table verification after agent loss. A DP calculation can now use multiple ordinary leased workers; see
[`cluster/QUEUED_TILES.md`](docs/QUEUED_TILES.md).
