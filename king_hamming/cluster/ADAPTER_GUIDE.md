# Supplying another solver

The cluster runtime is trusted infrastructure, not an arbitrary-code service.
A project opts in by installing a small Python adapter and registering it from
the top-level `adapter_config.py`. Queue clients may then select only the
program names that trusted code registered.

## Minimal lifecycle

Subclass `cluster.adapters.SolverAdapter` and implement:

1. `programs` and `validate()` for the canonical JSON specification;
2. `estimate()` for queue ordering (it need not predict wall time exactly);
3. `resource_requirements()` for per-host CPU and coordinator/worker memory;
4. `command()` to return a fixed executable argument vector;
5. `validate_result()` so corrupt or incomplete bytes cannot be published;
6. `runtime_files()` for every executable/module needed in a deployed bundle.

The generic agent owns process groups, affinity, lease renewal, stop escalation,
bounded output, storage, hashes, and publication. The executable should write
newline-delimited progress objects to stdout and reserve stderr for bounded
diagnostics. It must write its result only to the output path supplied by the
adapter.

If the solver can restart, also implement the checkpoint description, paths,
metadata validation, destination, and handshake hooks. These hooks describe
immutable committed state; they do not transfer files or decide retention.

## Workflows and distributed solvers

Simple solvers need no private database tables. A dependency graph may implement
`initialize()`, `enqueue()`, and `advance()`; all three execute inside the
leader transaction. Internal child specifications must still be canonical and
validated. Adapter-specific input routes are declared in `input_routes` and are
called only after the runtime verifies the live lease.

`required_nodes()` may request an atomic host group. `resource_requirements()`
uses this shape:

```python
{
    "coordinator_memory_bytes": 2 * 1024**3,
    "worker_memory_bytes": 1024**3,
    "min_cpu_count": 2,
}
```

The coordinator memory must include every local component on that host. Values
are checked by `resources.ResourceRequest`; the runtime keeps 2 GiB of host
headroom when memory is known. Whole-host and group leases are exclusive.
An adapter may opt into `allows_host_sharing()` and implement
`cpu_width(specification, available)` to choose a team from the remaining host
CPUs (zero means this task cannot fit). The scheduler atomically reserves the
disjoint `assigned_cpu_set` and aggregate memory. Supervisor slot IDs identify
lease loops, not fixed CPU masks. `worker_specification()` must use the granted
CPUs without exceeding the admitted memory budget; grants last for the entire
lease. `NodeCapacity.safe_slots()` is only an informational planning bound.

## Status and campaign policy

`status_details()` supplies compact strings for the operator CLI;
`augment_status()` may attach read-only structured fields with one bounded query.
Algorithm-independent campaign policy belongs outside `cluster/`, as in
`campaigns/king_hamming.py`. A campaign may inspect status and enqueue/control
runs through the public protocol, but it must not mutate the leader database.

## Reuse proof and checks

`tests/fixtures/demo_adapter.py` and `demo_solver.py` are the smallest working
example. `tests/test_adapters.py` copies their declared runtime into an isolated
bundle and drives queue, checkpoint, recovery, and result validation without
importing the DP or matching packages. Run the complete contract and integration
suite with:

```sh
make -C king_hamming/cluster check
```

Before deployment, add tests for invalid specifications, underestimated memory,
intentional stop, stale lease publication, corrupted results, checkpoint
fallback, and a copied-runtime launch.
