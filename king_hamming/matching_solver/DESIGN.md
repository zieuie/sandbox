# Matching solver design

Status: the local C engine, pinned parallel BFS and path proposals, KHM1
writer, independent verifier, and hydration utility are implemented and checked
against the preserved reader. Local phase-boundary checkpoints and validated
resume are implemented. The generic cluster adapter now queues pinned fields,
replicates committed phase checkpoints, restores on another worker, and stores
verified KHM1 artifacts. A bounded multi-node native engine now splits one
attempt across congruence shards, with one native process per node and native
threads across that node's allocated cores. Python only establishes and passes
cluster-owned streams. The
generic cluster reserves 2-9 agents, assigns each shard its actual CPU allocation
up to the requested ceiling, replicates shard-count-independent KHS1 commit images, and restarts a fenced group after partner failure.
Small frozen KHD1 compatibility fixtures remain in `../examples/`; the
superseded reference engines have been removed.

## Inputs and mathematical conventions

A matching run consumes a previously saved, immutable KHD1 DP artifact. It does
not recompute or alter the DP split. The run also identifies one primitive
polynomial, either explicitly supplied or generated automatically. The generator
is always X: label 0 is zero, and label z > 0 denotes X^(z-1). In particular,
label 2 is X. Prefix uses only the paper's leading half of the coefficients,
following the existing production field builder and preserved reference.

Reuse the field implementation in `../dp_solver/` rather than introduce a
second field arithmetic implementation. Keep the matching engine, artifact
codec, independent verifier, and cluster adapter separately usable.

The graph stays implicit. In DP run order, expand each repeated (a,b,t) step
into t consecutive cosets, then enumerate prefix residues 0 through a-1 and
suffixes 0 through F-1, where F = p^floor(r/2). A request identifies its coset
and SUD cell. Each cell contains F labels in ascending label order. Neighbor k
is the k-th cell label shifted by the inverse coset power of X. For label z > 0
and coset i, the shifted label is

    1 + ((z - 1 + (q - 1) - i) mod (q - 1)).

Zero stays zero. Use sufficiently wide intermediates for this expression.
Compressed DP blocks should supply request descriptions without allocating an
expanded pair of integers for every request.

## Success certificate

Any matching covering all left requests is acceptable. Its certificate stores
one neighbor index k in 0..F-1 per request, in the canonical request order above.
It need not contain the full construction grid, coefficient vectors, or explicit
left/right endpoint pairs.

For N left requests, the uncompressed packed choice payload needs

    ceil(N * ceil(log2(F)) / 8) bytes.

A header records the DP artifact hash, p and r, the primitive polynomial in
low-degree-first coefficient order, request count, outcome, and format version.
The format fixes generator X, request enumeration, cell ordering, and bit
packing. A checksum covers the complete artifact. The DP artifact remains a
separate mathematical dependency. The current cluster adapter carries its
small exact bytes in the queue specification; a future CAS dependency protocol
can replicate the DP artifact separately.

This is a compact encoding of the entire matching, not a constant-size proof.
It is substantially smaller than the full grid and uses fewer bits per request
than storing a field label, which requires ceil(log2(q)) bits. Additional
compression or structured encodings can be explored later; correctness must not
depend on finding a special matching or a reproducible random seed.

The independent reader defines the established packed-neighbor KHM1 conventions.
The production writer uses the same byte conventions and its artifacts pass
that reader. Introduce a new version only for an actual
incompatible change.

## Verification and unsuccessful field attempts

A separate verifier checks the referenced DP artifact, primitive polynomial,
canonical dimensions and encoding, and each decoded edge. It verifies that every
request has a choice in range and that no two requests use the same right label.
It reconstructs the field and graph without rerunning matching search. A q-bit
occupancy bitmap is sufficient for uniqueness; field reconstruction has its own
memory requirements. Verification establishes matching validity, not DP
optimality; those are separate checks.

A timeout or incomplete search does not prove that a polynomial fails. Only an
exact completed search with a verified Hall witness may record an obstruction.
Save each such encountered failure with its DP identity, polynomial, and
deficient left set. The complete neighborhood is reconstructed during independent
verification. Then try the next primitive polynomial in automatic mode. Keep
failed attempts when a later polynomial succeeds. Do not
run a separate search for polynomial sensitivity. Explicitly pinned polynomial
runs report their outcome rather than silently changing the polynomial.

## Implementation stages

1. **Implemented:** a local C engine checked against the reference on small fields.
   Start with an implicit Hopcroft-Karp engine, iterative path searches,
   uint32 labels with an explicit unmatched sentinel, and wide size counters.
   Serial correctness, the compact writer, hydration utility, and independent
   verifier are checked at this stage.
2. **Implemented locally:** pinned workers scan BFS levels and explore
   vertex-disjoint augmenting paths using the DP solver's physical-core-first
   placement policy. One owner claims each visited left vertex; atomic bit
   claims reserve distinct free right endpoints. Every proposal reads one
   immutable pair-array snapshot. After joining workers, the main thread
   checks and commits complete paths. When claim conflicts yield no proposal,
   the exact serial search finishes that phase; this preserves maximum
   cardinality. Threads share one field and matching state, with bounded
   claim arrays and worker stacks. Lower-overhead persistent workers,
   long-layer progress reports remain future efficiency work. Local durable
   phase checkpoints and cross-worker-count resume are implemented.
3. **Implemented:** register a matching adapter with the generic cluster. Distribute
   independent field attempts across workers. Reuse leases, immutable storage,
   replication, intentional stop, and retry fencing. Keep DP queue behavior intact.
4. **Implemented as a bounded native engine:** distribute one matching across
   2-256 shard processes, including 2-9 reserved cluster machines. Each node
   builds one compact native field and scans across its allocated cores. A C
   coordinator owns canonical pair state, path conflict resolution, Hall
   extraction, KHS1, and KHM1. Every shard keeps replicated committed pairs and
   distances, reduces its owned BFS frontier locally, and proposes complete
   augmenting paths without exporting edge labels. Python only establishes local or
   lease-authorized streams and passes their descriptors through. After every
   native phase barrier, KHS1 records the complete state and the generic
   cluster replicates it before continuing. The leader reserves and fences the
   requested agent group for each `match_distributed` lease. Canonical KHS1
   assignments can be repartitioned across a different shard count on recovery.
   Independently matching fixed tiles cannot guarantee global coverage.

In a local 13^5 check with 371,293 requests, four threads produced an
independently verified complete matching. Two direct C-kernel timings on CPU
cores 1-4 were 0.349-0.359 s with four threads, versus 0.660-0.682 s with
one worker. The parallel search scanned 96-102 million edges versus 155.5
million in the serial search. These are one-host measurements, not throughput
promises for larger fields.

The generic cluster transports remote shards over lease-authorized streams
served by reserved partner agents. This avoids pairwise SSH credentials
between workers. An isolated four-agent/eight-core regression checks group fencing,
replicated checkpoint restoration, large phase frames, and final KHM1
verification. A separate private two-host cluster test passed on `.107` and
`.108`: 13^5 completed in 10.74 seconds with two threads per node, including a
deliberate partner failure. A fresh lease restored 368,414 assignments from two
checkpoint replicas, and the final KHM1 independently verified. The running
household DP deployment was not changed by this test.
See [DISTRIBUTED.md](DISTRIBUTED.md).

## Local checkpoint and resume

A `KHC1` checkpoint is written only after a fully committed matching phase.
It binds the exact DP SHA-256, primitive polynomial, p/r/q/F, request count,
and compact request blocks. It stores the phase count, scan count, cardinality,
and each left assignment plus selected neighbor index. Right ownership is
reconstructed on load, then every edge, pairing, and cardinality is checked.
A streaming 64-bit checksum detects accidental file damage; the cluster's
content-addressed transport separately uses SHA-256.

The writer flushes and syncs a temporary file before atomically replacing the
local checkpoint path and syncing its directory. A failed or torn write cannot
replace the previously committed file. A requested pause exits 3 after a
checkpoint and does not publish a final certificate. Resume may use a different
thread count because no in-flight BFS or proposal scratch is stored.

The local single-host engine checks its 30-minute cadence at phase boundaries.
The distributed engine additionally commits bounded root ranges during
direct-path phases, checks the interval at those boundaries, and can resume the
canonical partial matching with a different shard count. Cluster replication
and multi-generation retention are implemented through the adapter.

## Memory, recovery, and storage

Do not materialize all graph edges. Admit jobs against field storage, both
matching directions, frontier/search scratch, thread stacks, and checkpoint
buffers. The current field cells alone occupy 4q bytes: near the uint32 limit,
that approaches 16 GiB, before matching state. Such targets require sharding,
another bounded-memory representation, or an adequately provisioned host.
The one-field-per-machine rule means one shared copy or owned shard, not one
copy per thread or an unconditional full copy on every worker.

The local engine checkpoints at the configured phase-boundary interval; the
cluster adapter distributes and retains those snapshots. It reports cardinality
after each completed phase, while agent liveness is tracked independently.
The distributed direct-path phase reports bounded commit progress; later
augmentation phases still report at their complete barriers. Keep shutdown intentional and
separate from failure recovery. Do not assume automatic agent restart or machine
reboot is already installed.

The cluster adapter stores immutable certificates on worker disks using
the existing content-addressed storage and replication policy (currently three
final-artifact replicas). The leader maintains their index and hashes. Reruns
and polynomial-failure records have separate run identities. Both full
matchings and certified Hall obstructions are independently verified results;
only full matchings satisfy the construction goal.

## Current cluster boundary

`adapter.py` owns queue validation, runtime packaging, estimates, and checkpoint
manifest identity. Local `match` uses `cluster_solver.py` around one native
kernel. Distributed `match_distributed` uses `native_coordinator.py` only to
establish local or lease-authorized process streams and pass their descriptors to
the C coordinator. C parses KHD1 and owns graph search, matching state, KHS1, and
KHM1. The generic cluster owns leases, CPU affinity, replicated blob storage,
checkpoint history, and final artifact indexing. Python independently verifies
the final artifact before publication.

Native processes report `getrusage` user-plus-system CPU time and Linux peak RSS
at orderly shutdown. The leader validates each reported shard index against the
reserved group and persists it by lease attempt in `resource_usage`; recovery
therefore preserves both the failed and replacement attempt's tuning history.

Each queued `match` job pins a primitive polynomial and references exact KHD1
bytes by SHA-256. The DP bytes travel inline with this first adapter; the
queue request is capped at 700 KiB of decoded DP data. KHC1 manifests bind
the DP digest, graph blocks, polynomial, committed phase and matched count.
The generic agent copies that immutable file into storage while the native
engine waits for acknowledgment, then can restore it under a new lease. On
resume the C kernel checks every saved edge against the rebuilt field.

A pinned Hall obstruction is a successful *calculation* and a certified KHM1
artifact, even though it is not a full matching. Automatic attempts across
polynomials remain a later stage. The distributed half-hour cadence is checked at bounded direct-path commits and
at all completed augmentation barriers. The local single-host engine remains
phase-boundary based.

## Follow-up: cluster-wide matching and queue control

The bounded native `match_distributed` engine now uses all allocated cores and
machines. A two-worker/four-thread-per-worker 13^5 benchmark fell from 115.7
seconds through 23.81 seconds to 2.02 seconds after shards began reducing BFS and
proposing paths locally. A four-worker/two-thread run took 1.83 seconds. Before
submitting another giant calculation (for example `2^25`), measure the reduced
protocol's replicated-state memory on a larger field. As part of that work, add per-run
operator controls so the queue can be changed without globally
stopping dispatch: cancel a queued or active run, pause/resume one run while
preserving its checkpoint, and change a queued run's priority. These operations
must leave unrelated leases and workers running, expose their resulting state
in status, and prevent cancelled runs from being dispatched after a later
campaign-wide resume.
