# Generic cluster design

This directory provides queue ownership, leases, process supervision, CPU
placement, content-addressed storage, replication, recovery, and operator
commands. Mathematical kernels live in [dp_solver/](../dp_solver/DESIGN.md), and
DP-specific integration lives in [dp_solver/](../dp_solver/DESIGN.md).
The cluster uses the Python standard library.

## Trusted solver adapters

`adapters.py` defines `SolverAdapter` and a trusted program registry.
Project composition in `../adapter_config.py` registers algorithm packages.
The agent does not choose DP executables or interpret native DP filenames;
the leader does not know DP dimensions or rectangle dependencies.

An adapter supplies specification validation, resource estimates, solver argv,
input preparation, checkpoint interpretation, result validation, and optional
workflow hooks. The cluster retains control of lease fencing, HTTP requests,
process groups, stop escalation, transfer integrity, and publication.
An adapter declares its runtime files so generic deployment can bundle the
appropriate algorithms without naming their packages or binaries.

Adapters are installed trusted Python code. Queue specifications select
registered programs and arguments; they cannot upload code or arbitrary shell
commands. An unrelated test adapter exercises the same queue and single-file
checkpoint transport without using DP layouts. A copied-runtime integration test
runs that adapter alongside distributed DP.

The built-in demo adapter tests the lifecycle. The configured DP adapter owns
whole-worker DP, distributed DP, and internal tile jobs. Its frontier controls
are delegated through the compatibility `kh.py campaign` command. The matching
adapter registers `match` and `match_distributed`. For the latter, the leader
atomically reserves the requested multi-agent group beside the coordinator lease;
reservations block other compute leases and are cleared on completion or fencing.
Each reserved node exposes one fenced native shard stream; that process builds
one field and uses its assigned CPU set for field construction and scans. A partner
agent restart or expired heartbeat fences the entire group. The adapter supplies
the solver command and KHS1 checkpoint validation, while the generic cluster
retains and replicates committed phase images. Partner agents expose
lease-authorized byte streams for trusted adapter-supplied worker commands.
The Python bridge passes those descriptors to the C coordinator without decoding
algorithm data; BFS, augmentation, matching state, checkpoints, and certificate
generation are native.
The leader checks the current reservation before the stream starts, and the
agent monitors the lease while serving it; pairwise SSH between workers is
not required.

The adapter resource response is normalized by `resources.py`, the single
authority for CPU counting, reserved operating-system memory, role-specific
fit, and theoretical disjoint-slot capacity. The current scheduler still grants
at most one lease per host. Keeping `safe_slots()` informational until leases,
scratch ownership, aggregate reservations, and fencing carry a slot identity is
intentional: reporting possible concurrency must not silently enable unsafe
oversubscription. See [ADAPTER_GUIDE.md](ADAPTER_GUIDE.md) for the reusable
project boundary.

## Durable queue and workflow extensions

SQLite stores canonical specifications, immutable calculation identity, retained
runs, lease history, node sessions, artifact descriptors, settings, and native
process resource measurements. `resource_usage` retains lifetime CPU
microseconds and peak RSS bytes by lease attempt and shard, so recovery history
does not overwrite the measurements from an earlier attempt. Identical
submissions normally reuse a run; explicit reruns retain another attempt.
Migrations add tables or columns without discarding history.

Dispatch orders work by explicit priority, adapter-provided estimated runtime,
then creation time. Estimates are ordering heuristics rather than wall-clock
promises. The generic `parent_run_id` column identifies internal child work,
letting status show parent calculations and active children independently of an
algorithm's private tables.

Adapters can initialize tables, prepare a newly enqueued root, and advance their
workflows inside the leader transaction. Leased input routes are delegated only
after generic ownership checks. The DP adapter retains the older `/v1/tile-input`
route for existing workers and supports `/v1/adapter-input` for new workers.
Different algorithms can supply different dependency and communication rules.

## Worker execution and affinity

Each agent owns at most one computation lease. It asks the adapter to adapt
operational settings, prepare inputs, and construct solver argv. The generic
supervisor launches a process group through the affinity wrapper, drains stdout
and stderr, polls control, and tracks completion or failure.

CPU placement selects physical cores before SMT siblings. On merlin/uther at
`.151`, the leader can reserve one physical core including both SMT siblings;
a colocated compute agent excludes it. Solver children use Linux parent-death
handling, preventing an agent crash from leaving an unsupervised calculation.

The adapter chooses checkpoint-handshake support, local versus fresh-lease retry,
result checks, and scratch cleanup. Cleanup runs only after durable publication
is acknowledged. Generic supervision never needs to know a solver's work filenames.

## Independent liveness and intentional stop

Node heartbeat, solver heartbeat, actual progress, and local checkpoint time are
separate. A responding agent does not certify a stalled solver. Solver stdout
contains newline-delimited JSON reports; stderr is drained into bounded
retained diagnostics. Status includes phase, computed and durable work, replicated
coverage, restored work, attempt number, heartbeat age, and result replica counts.

An independent solver reporter can remain responsive even while work is paused.
Health labels distinguish startup, response, missing heartbeat, five-minute
no-progress warning, and thirty-minute stall. These are diagnostic; automatic
host reboot is not implemented.

Global stop disables dispatch and latches stop requests on active leases.
Resume enables new dispatch without clearing old owners' flags, so a quick
stop/resume cannot be missed. Intentional exit 75 requeues work without failure
escalation. Successful completion during stopping remains retained. After the
configurable grace period, defaulting to 1800 seconds, an unresponsive process
can be killed while preserving previously committed restart state.

Checkpoint policy defaults to 1800 seconds and is independent of progress
reporting and worker count. The adapter defines its actual restart boundaries;
distributed DP currently checkpoints only through immutable tile completion.

## Ownership, storage, and recovery

Registration binds each node name to a new session UUID. Computation leases have
unique tokens and renewal deadlines. Protected mutations validate ownership in
the same transaction as the update. Expired or reassigned owners cannot publish
late progress or completion. The leader expires leases even without new requests.

Workers serve content-addressed blobs and replicate while computing. Bulk files
move directly between peers; the leader holds hashes, sizes, and locations.
Transfers are bounded, resumable, and checked by SHA-256. Confirmed corrupt
replicas lose their claims, and another source can supply the content.

Generic checkpoint manifests identify a calculation, committed coverage, and
immutable files. The adapter interprets coverage, file sizes, layout and restart
metadata. Capture keeps the solver quiescent; transfer and control remain active.
Only complete verified replicas count toward recovery durability.

Recovery tries available checkpoints newest first, retaining older fallback
images. Restore copies CAS members into mutable files and atomically installs
at the adapter-selected destination; it never hard-links mutable state to CAS.
Retention normally keeps three distinct live two-copy snapshots. Restore pins,
shared artifact references, and storage transactions protect publication and
restoration from garbage collection races.

New agent incarnations revalidate retained disk contents before reclaiming
replica ownership. The index survives leader restart. Final artifacts target
three worker copies when enough workers are available. Adapter validation checks
results before publication, and `verify_artifact.py` delegates manual verification
to the registered algorithm with optional checksum and work limits.

## Code map and operational boundaries

| Component | Responsibility |
| --- | --- |
| `adapters.py` | Solver contract, trusted registry, lifecycle dispatch |
| `leader.py`, `common.py`, `resources.py` | HTTP control, SQLite queue, identity and admission arithmetic |
| `agent.py`, `affinity_exec.py` | Leases, supervision, CPU placement |
| `blob_store.py`, `deployment.py` | CAS transfers, storage transactions, SSH deployment |
| `checkpoints.py`, `recovery.py`, `retention.py` | Generic manifests, fencing, restore, retirement |
| `kh.py`, `verify_artifact.py` | Operator commands and adapter-selected verification |
| `tests/fixtures/demo_adapter.py`, `tests/fixtures/demo_solver.py` | Deterministic test-only lifecycle backend |
| `../campaigns/king_hamming.py` | Project policy layered above the generic runtime |

Legacy DP launch and experiment filenames here are thin compatibility wrappers;
their implementations are in `dp_solver/`. Existing campaign state remains in
`deployments/`. New bundles include configured adapters; running deployments are
not hot-replaced when source code changes.

Hard global disk quotas, automatic agent/host restart, TLS/authentication, and
production service installation remain open. Algorithm-specific limits, such as
DP's retained intermediate tiles and recovery after losing every copy, are
recorded in the corresponding adapter design. Current commands are in
[README.md](README.md); historical notes remain in [docs/](../docs/).
