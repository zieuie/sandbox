# DP solver design

This directory owns the mathematical kernels and independent result utilities.
The [cluster](../cluster/DESIGN.md) owns generic scheduling, process supervision,
and replication. The Python adapter in this directory supplies DP-specific
commands, estimates, checkpoints, and tile orchestration. Frozen small KHD1
fixtures in [`examples/`](../examples/) provide compatibility cases. Production
DP, field construction, and matching are implemented.

## Exact DP and stable choices

For an odd prime power `q = p^r`, let `F = p^floor(r/2)` and `B = pF`.
The DP stores the best score for each pair of budgets `(u,v)` up to `(B,B)`.
A transition `(a,b,t)` consumes `(a*t,b*t)` and contributes `t*omega(a,b,t)`.
Candidates are considered in the paper's ascending `a,b,t` order, and only
strict improvements replace the recorded choice.

`src/transitions.c` groups transitions with identical costs, retaining the earliest
maximum-gain member of each group and restoring original enumeration order.
This preserves every DP value, choice, and reconstructed split. Original choice
IDs are retained, making raw and reduced modes checkpoint-compatible.
`--raw-transitions` provides the reference scan. The historical
[transition notes](../docs/TRANSITIONS.md) contain the proof and measurements.

## Local and distributed kernels

`kh_dp_local` owns complete file-backed arrays: an unsigned 64-bit score and an
unsigned 32-bit choice per cell. Logical payload is `12(B+1)^2` bytes.
Tiles run in dependency order. Within a tile, a persistent pinned pthread pool
shares the arrays and evaluates independent cells on each antidiagonal.
Barriers publish writes before dependent cells execute.

`kh_dp_tile` is a separately reusable kernel. Its input is a native unsigned
64-bit predecessor rectangle, including a halo of at most `p^2` in each budget
direction. It recomputes the tile interior and atomically publishes tile-only
values, choices, and layout metadata. Its admission calculation includes halo,
choice, transition, stack, and fixed reserve bytes; the default address-space
ceiling is 2 GiB. It has no partial-tile checkpoint.

The cluster assembles predecessor halos, assigns tile leases, and reconstructs
the final split. The solver has no dependency on a leader or network connection.
Native tile interchange requires compatible layouts and byte order.

## Local durability and progress

The local restart cursor advances only after its tile state is flushed and
checkpoint metadata is published. Replayed cells are reset, so partially
persisted work cannot affect a resumed result. A stop finishes the active tile,
checkpoints, and exits 75. A different worker count may resume the same state.

An independent reporter emits newline-delimited JSON on stdout; diagnostics go
to stderr. `done` counts completed positive-budget cells, including uncommitted
work. `checkpoint_done` counts coverage of the durable local cursor. Thus
`0 <= checkpoint_done <= done <= total`; neither counter establishes replication.
Reports arrive every ten seconds by default even without increased progress.

Phases include computing, checkpointing, snapshotting, reconstructing, and
stopped. Initial transition construction precedes reporter startup.
In agent-only `--checkpoint-handshake` mode, the pool remains paused after a
checkpoint event until stdin receives a newline acknowledging immutable snapshot
capture. Reporter and agent control activity continue while the pool is paused.

## One shared field

`kh_field_t` contains one immutable SUD partition using four bytes per field
element. Pinned workers share it; no full coefficient or logarithm table is kept
alongside it. Two parallel passes count labels and then write disjoint segments,
producing identical label order regardless of worker count.

Zero has label zero. Label one is 1, label two is X, and subsequent labels are
successive powers of X. Polynomial coefficients are stored in increasing degree
order. The suffix packs the lowest `floor(r/2)` coefficients in base p; the
prefix sums the remaining leading coefficients modulo p, following the paper.
Each of the B SUD cells contains F labels.

Primitive polynomial testing requires X to have exact order `q-1` in the
quotient. Generation enumerates monic candidates, always keeping X as the
generator. Construction memory includes the `4q` table, per-worker counters,
stack allowances, and a fixed reserve. The historical
[field notes](../docs/FIELD.md) give the algebraic argument and admission formula.

## Separate, compact results

Local C DP emits a run-length JSON split. `artifacts.py` encodes and decodes the
portable KHD1 binary equivalent: prime, degree, score, repeated `(a,b,t)` choices,
and a SHA-256 checksum. It includes no DP matrix or field table. Cluster
reconstruction can emit KHD1 directly. Field selection and matching will consume
the separate DP result rather than requiring another DP calculation.

`print_dp.py` prints or converts artifacts. Format validation checks canonical
encoding, dimensions, feasibility, and gain. `verify_dp.py` separately recomputes
the optimum and ordered split; a valid checksum alone does not prove optimality.

## Code map and checks

| Component | Responsibility |
| --- | --- |
| `src/common.c`, `include/kh_solver.h` | Dimensions, arithmetic, and shared interfaces |
| `src/transitions.c` | Exact transition construction and reduction |
| `src/threads.c` | Persistent worker pool and CPU affinity |
| `src/progress.c` | Independent progress reporting |
| `src/dp_local.c` | File-backed DP, local checkpoints, reconstruction |
| `src/dp_tile.c` | Bounded immutable tile evaluation |
| `src/field.c`, `src/field_build.c` | Shared field library and CLI |
| `artifacts.py`, `print_dp.py`, `verify_dp.py` | Portable artifacts and independent inspection |

Checks compare complete arrays and artifacts across thread counts, tile sizes,
raw/reduced transitions, and interrupted/resumed calculations. Tile assembly
matches the full recurrence, field partitions match independent polynomial
arithmetic, and binary artifacts pass both independent decoders on feasible examples.
Command examples and checks are in [README.md](README.md).

## Edge bands

A finished tile publishes, besides its packet, up to three compressed edge bands
(`bands.py`): `bottom`, `right` and `corner`, each `p^2` cells thick and clipped to
the tile. A successor's halo reaches at most `p^2` cells into a predecessor, so it
needs only one band from each: `bottom` from the tile above, `right` from the tile
to the left, `corner` from the diagonal one (`tiles.band_kind`). `build_halo`
accepts a whole-tile file or a band `piece_t` and refuses one that does not cover
the halo. The leader indexes bands by packet hash (`tile_bands`) and lists one in a
descriptor only while it has a live replica; the packet is always listed too, and
any band problem falls back to it. Details and measurements are in
[DP_NETWORK_LOCALITY.md](../docs/DP_NETWORK_LOCALITY.md).

## Cluster integration

The Python modules here adapt the C kernels to the generic cluster lifecycle.

## Lifecycle boundary

`adapter.py` implements `cluster/adapters.py`'s `SolverAdapter` contract for
`dp`, `dp_distributed`, and internal `dp_tile` jobs. Project composition in
`adapter_config.py` registers this adapter. The cluster does not select DP
executables, interpret coefficient dimensions, or recognize native DP filenames.

| Hook | DP responsibility |
| --- | --- |
| `validate`, `estimate` | Check dimensions/internal admission and estimate raw work |
| `worker_specification` | Cap operational thread count to assigned CPUs |
| `command`, `prepare` | Select the executable and write private fenced task inputs |
| `checkpoint_handshake` | Select paused snapshot capture behavior |
| `retry_elsewhere`, `cleanup` | Choose retry location and retire acknowledged scratch |
| `checkpoint_description`, `checkpoint_paths` | Describe member sizes, coverage and quiescent files |
| `validate_checkpoint_metadata`, `checkpoint_destination` | Interpret native restart metadata and choose restore placement |
| `initialize`, `enqueue`, `advance`, `inputs` | Own the tile DAG and authorize input descriptors |
| `validate_result`, `verify_result` | Check final split feasibility or independently recompute optimality |
| `runtime_files` | Declare Python modules and binaries required in a deployment |
| `configure_campaign`, `campaign_entries` | Supply DP-specific table-frontier CLI options and entries |

Adapter hooks run within the leader's existing transaction when they modify
workflow state. They must preserve fencing: input descriptors are supplied only
after generic lease ownership validation. `/v1/adapter-input` is the generic
input route; `/v1/tile-input` remains an accepted alias for existing deployments.

Operational CPU adaptation does not change the submitted calculation identity.
Result validation before publication checks split format, requested dimensions,
feasibility and gain. Independent optimum verification is reserved for the
manual verification hook, with a configurable work limit. Tile packet layout
validation remains inside the distributed helper.

## DP-specific state

`checkpoints.py` owns KHDPCHK1 native metadata, values/choice widths, file names,
restart cursor interpretation and byte-order requirements. Generic cluster
checkpoint code checks identity, file hashes and limits, transfers immutable
members, and installs mutable copies at the destination selected by this adapter.
Existing KH-CHECKPOINT-1 manifests remain usable without conversion.

`scheduling.py` owns prime-power enumeration and DP cost estimates. `tiles.py`
owns rectangles, dependency geometry and streaming halo assembly.
`distributed.py` owns the existing `distributed_tiles` table and advances roots
when predecessor packets have two live verified copies. Child runs also set the
generic `parent_run_id` field for status filtering; initialization backfills that
field for older databases without replacing history.

`distributed_solver.py` fetches predecessor packets, builds a bounded halo,
invokes the C tile kernel, and reconstructs the optimal run-length split after
the DAG completes. It imports generic request/transfer and affinity services.
Its default two-GiB tile admission, native-layout constraints and immutable-tile
restart boundary are unchanged by this extraction.

## Utilities and compatibility

`launch_dp.py` owns the household DP frontier and compact result collection;
`tile_driver.py` owns the separate local/SSH tile experiment driver. Household
experiments also live here. Their SSH transport and runtime bundling use generic
`cluster/deployment.py` helpers.

The older cluster command paths delegate through thin wrappers. Existing worker
bundles remain valid and running campaigns do not require hot code replacement.
New bundles contain this package and the project adapter configuration. Persistent
campaign state stays in `cluster/deployments/`.

## Adding matching

A matching package can implement the same interface and register itself in
`adapter_config.py`. It supplies its command, DP-artifact input preparation,
field/matching state layout, checkpoint rules, estimates and certificate verifier.
The leader and agent retain their generic lease and supervision lifecycle.

Distributed matching must supply its own workflow/communication hooks; it cannot
reuse DP's fixed rectangle dependencies. Algorithm-specific extension points are
trusted Python code installed with the project, not user-uploaded code received
from a queue submission.

Current execution and recovery limits are unchanged: intermediate tile packets
are retained; losing every copy blocks dependents; automatic host reboot and
hard global disk quotas remain unfinished. Historical mathematical and experiment
notes are preserved in [docs/](../docs/).
