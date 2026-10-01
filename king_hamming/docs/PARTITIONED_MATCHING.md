# Matching with ownership partitioned across machines

Design proposal, 2026-09-30. This is the specification for a future separate
solver, tentatively `partitioned_matching_solver/`, exposed as `match_partitioned`.
Neither name is registered or implemented. The existing replicated matcher
remains an independent engine and a comparison oracle.

The goal is exact maximum-cardinality bipartite matching when one machine
cannot comfortably hold the working arrays. Each large field, matching, and
search array has one computational owner. Workers communicate discoveries and
assignment changes in batches. The coordinator holds control metadata and
reductions, without a complete matching or search frontier.

The principal risk is network traffic and repeated graph scans. Fitting in RAM
is necessary but does not establish a useful time to solution. The first
implementation must measure both before attempting a large production field.

## 1. Mathematical and compatibility contract

Inputs are an immutable KHD1 DP artifact, its SHA-256, and one pinned primitive
polynomial with generator X. Preserve canonical request IDs and neighbor indices
from [the current matching design](../matching_solver/DESIGN.md).

Write `q = p^r`, `F = p^floor(r/2)`, and `N` for the number of left requests.
Right vertices are field labels `0 .. q-1`; left vertices are canonical request
IDs `0 .. N-1`. Each left request has a coset index and a SUD cell containing
exactly F labels in ascending label order. For cell label z and coset c,

```text
neighbor(left, k) = 0                              if z == 0
                   1 + ((z - 1 + q - 1 - c) mod (q - 1)) otherwise
z = cell(left)[k]
```

Use wide intermediates. Do not store all `N*F` edges. Compressed DP blocks map
canonical IDs to requests; partitioning does not renumber the mathematical graph.
Initially retain the current uint32 vertex bounds and uint16 choice bound, with
explicit validation and an unambiguous unmatched sentinel. Counts, offsets,
message sequences, and byte arithmetic use checked uint64 operations.

A complete result is an ordinary KHM1 certificate, accepted by the existing
independent verifier. An obstruction applies to this DP split and polynomial;
it is not a claim about every primitive polynomial. Campaign policy may try
another polynomial after a certified obstruction. Resource exhaustion, network
failure, a paused search, and an incomplete search never mean no matching exists.

## 2. Ownership and data placement

Use many fixed virtual partitions, mapped onto a smaller set of machines. Their
identities survive a change in machine count. Choose the mapping from both RAM
capacity and measured compute throughput; freeze it for a search/commit epoch.

| Data | Computational owner | Reason |
| --- | --- | --- |
| Complete SUD cells | Weighted ranges of cell IDs | Every neighbor label for a request can be read locally. |
| Left requests and their state | Owner of that request's SUD cell | All requests reusing one cell share its immutable labels. |
| Right vertices and their state | Independently weighted ranges of right IDs | One authority arbitrates discoveries and matching assignments. |
| DP blocks, polynomial, ownership map | All workers | Small immutable routing metadata. |
| Search counts, phase and commit decisions | Coordinator | Bounded control traffic, without per-vertex arrays. |
| Immutable field blocks and checkpoints | Replicated blob storage | Durable copies are separate from live computational ownership. |

All requests for a cell are owned together even when their canonical left IDs
are interleaved with other cells. Implement `owner(left)`, `local_index(left)`,
and the inverse with DP block prefix counts and cell-range metadata. Avoid an
additional N-entry hash table or ID translation array. Test these mappings
exhaustively on small graphs. A partition's memory estimate must use its actual
request count; equal numbers of cells need not mean equal amounts of work.

Use one native C worker process per participating machine and a persistent pool
of pinned compute threads sharing that process's owned state. A small coordinator
may run beside one worker. Python may launch processes and establish streams;
it does not parse edge batches or matching arrays.

### Build the field without a full copy on any worker

Split the nonzero exponent labels into ranges. A range builder computes its
initial power of X by exponentiation, advances by multiplication by X, classifies
each element using the existing prefix/suffix convention, and sends `(cell,label)`
batches directly to the cell owner. Assign zero exactly once.

Each owner allocates only its cells, inserts received labels into bounded
per-cell slots, and sorts each cell in place by label. Disjoint generator ranges,
primitive-polynomial validation, exact cell counts, and total coverage are checked
before the blocks become usable. Overflow of a cell is a correctness failure.
Reuse arithmetic primitives only after separating them from the current builder
that allocates `4*q` bytes on every process.

Store completed field blocks by polynomial, parameters, cell range, and format
version, with checksums and replicas. Reuse them after retries and ownership
changes. No matching mutation can alter these blocks. Construction queues and
sort scratch must have explicit bounds; a hidden full-size staging copy would
defeat the memory model.

## 3. First exact algorithm: batched augmenting forests

Start with a level-synchronous multi-source BFS forest. It is easier to audit
than concurrent distributed DFS with vertex locks. A later engine may use the
same ownership and checkpoint layers for full Hopcroft–Karp blocking phases.

The initial algorithm below is exact, but **does not claim the Hopcroft–Karp
complexity bound**. Selecting one path from each discovered tree need not find a
maximal collection of shortest augmenting paths. It may require substantially
more phases. The [original Hopcroft–Karp paper](https://doi.org/10.1137/0202019)
gives the stronger bound for its blocking-phase algorithm; benchmarking determines
whether the simpler forest engine is sufficient here.

### A. Freeze a committed matching

An epoch starts with mutually consistent `mate_left`, `choice_left`, and
`mate_right` arrays. They remain immutable throughout BFS and path selection.
Initialize every unmatched left vertex as a root with distance zero and its own
root ID. Clear the per-epoch visited and sent bitmaps; initialize distance/root
state only as required by a documented reset scheme.

### B. Expand one complete BFS level

1. Left owners scan all unmatched outgoing edges of the current frontier. Skip
   the currently matched edge. Tasks carry `(left, root, level, choice range)`
   so even one high-degree left vertex can be scanned by multiple local threads.
2. Route candidate records `(right, predecessor_left, choice, root)` to right
   owners. A right owner accepts only the first discovery of that right vertex
   during this BFS, recording its predecessor and choice.
3. If the right vertex is matched, route its mate to that left owner as a
   next-level discovery carrying the same root. Its predecessor right is
   recoverable from its immutable `mate_left` entry. If the right is free,
   report a free endpoint to the root's owner.
4. Finish sending and consuming every batch for this level before advancing.
   If any free endpoints were reached, stop after this complete level and choose
   paths. Otherwise advance the next frontier, or finish with a Hall witness
   when it is empty everywhere.

One predecessor per right is sufficient for reachability. Every matched left
vertex can be entered through only its unique matched right vertex. Thus each
discovered nonroot left vertex also has exactly one predecessor and root.
Owners publish a right's predecessor before publishing its discovery. A shard
can serialize this through owner-thread queues or use explicit atomic claim and
publication states; a partially initialized predecessor must never be visible.

Production tie choices may depend on arrival order. Provide a deterministic
single-thread/sorted-batch mode for small tests, without requiring production
runs on different machine counts to produce identical matching choices.

### C. Select disjoint paths and prepare edits

Each root owner chooses at most one free endpoint from its tree. Distinct trees
have disjoint vertices, so these paths are mutually vertex-disjoint. Walk each
chosen path backward through right predecessors and the frozen left mates.
Route bounded batches of path steps directly between owners. Do not send a
whole path, or every path, to the coordinator.

Each involved owner stages its local assignment changes with expected old
values, epoch, and path identity. Verify the unmatched endpoints, predecessor
chain, selected edges, lack of repeated vertices, and agreement of left/right
edits. The chain's left distances must decrease while walking toward the root.
A long path is streamed; it does not require a per-thread N-entry stack. Bound
the number of paths traced concurrently and spill prepared deltas to bounded,
accounted temporary storage if necessary. Reaching a resource cap is a resource
failure, never an obstruction.

### D. Commit the batch

All owners acknowledge preparation. The coordinator issues one numbered commit
decision; owners install the staged edits and acknowledge that commit. No new
search starts until every owner has installed it. Global cardinality increases
by exactly the number of selected paths; both matching directions must agree.
The coordinator retains acknowledgments and counts, without receiving the full
assignment arrays.

A failed participant aborts the whole live epoch. The group is fenced and
restored from the last durable checkpoint; survivors do not continue from a
partially installed commit. This deliberately avoids an additional distributed
transaction recovery protocol in the first implementation.

### E. Repeat, or certify the obstruction

Every successful batch increases cardinality by at least one. Rebuild the BFS
forest against the new matching. If no free right vertex is reachable after an
exhaustive search from all unmatched left roots, the matching is maximum.

Let S be the visited left vertices and T the visited right vertices. Every
neighbor of S is in T: unmatched edges were scanned, while a visited nonroot
left's matched right is already its predecessor. Every right in T is matched
back into S. Consequently `|S|-|T|` equals the number of unmatched left roots
and is positive when the left side is not fully matched. Emit S and the partial
matching as the existing KHM1 Hall certificate. The independent verifier
recomputes its neighborhood. An early-stopped or sampled BFS cannot supply S.

### Optional fast start

Before full BFS, use bounded rounds of direct free-right proposals: each
unmatched left has at most one outstanding proposal, right owners grant at most
one left each, and reciprocal assignments use the same commit barrier. Advance
through choice windows while these rounds make useful progress. Stop the fast
start on a measured low-yield threshold and run exact BFS. Exhausting a window
or losing competing proposals never proves an obstruction.

## 4. Make communication proportional to discoveries where possible

Sending a record for every implicit edge is unacceptable as the default plan.
For the saved `2^29` input, `N=q=536,870,912`, `F=16,384`, and `N*F` is
8,796,093,022,208 edges. A hypothetical 16-byte record per scanned edge would
be **128 TiB for one complete scan**, before framing or retries.

Use an exact `sent_right` bitmap on each sending machine for the lifetime of a
BFS. Only the first local encounter of each right vertex needs to be sent:
alternative parents are unnecessary for the forest's reachability argument.
An atomic test-and-set occurs only when the corresponding message is safely
retained for delivery; bounded queues apply backpressure. A group failure
discards the entire epoch. Reset this bitmap after every matching commit.

This is a deliberate small replicated structure: `q/8` bytes per machine,
64 MiB at `2^29`, while the multi-byte matching/search arrays remain owned.
Configure and account for its cap. If it does not fit, use bounded exact caches
and permit duplicate messages; eviction changes performance, not correctness.
A Bloom-filter positive alone must never suppress a discovery.

With W fully deduplicating senders, candidate count is at most `W*q` per BFS,
across all its levels. For nine machines and 16-byte candidates that is at most
72 GiB including owner-local candidates, approximately 64 GiB crossing machines
under balanced routing. This is a bound on candidate payload only: frontier
forwarding, path traffic, framing, and retries are additional. It is still a
substantial network cost, and edge generation can still scan all `N*F` edges.

Batch by destination, use bounded credit-controlled queues, and process a
message batch locally before forwarding further discoveries. Optionally send
exact reached-right bitmap deltas to suppress further candidates at other
senders, but measure whether that reduces total bytes. Keep caches tied to the
matching/search epoch so stale information cannot suppress needed edges.

## 5. Protocol and barriers

Use bounded binary frames over persistent peer streams. The cluster leader is
not the bulk-data router. Existing group streams are primarily coordinator-to-
partner; direct owner-to-owner channels need an explicit generic runtime
extension with lease-scoped peer authorization, fencing, and bounded buffers.
A coordinator relay may be used in a small correctness harness, not assumed
scalable in the deployment design.

Every frame identifies the group lease/incarnation, ownership-map version,
matching epoch, BFS level or commit number, sender, destination, sequence,
record count, length, and integrity check. Reject stale generations and invalid
IDs before mutation. Message families cover field construction, discoveries,
next-frontier updates, endpoint election, path tracing, prepare/commit,
checkpoint descriptors, reductions, and control.

A level completes only when all producers have closed that level, all sequenced
data through their closing markers has been consumed, and all derived frontier
updates have been acknowledged. Include locally generated work and helper tasks
in the counts. An empty queue, idle CPU, or elapsed timeout is not termination.
Keep receive/control progress alive while senders are credit-blocked; otherwise
all-to-all traffic can deadlock. Timeouts diagnose liveness or fence a failed
group, but cannot establish a mathematical result.

## 6. Memory budget

Use structure-of-arrays layouts so C padding does not silently inflate the
following initial allocation model. These are proposed capacities, not measured
RSS or a promise that every phase fits them.

| Owned allocation | Bytes |
| --- | ---: |
| Left mate right ID, selected choice | `6*N_i` |
| Left BFS distance and root ID | `8*N_i` |
| Best free endpoint per root, dense upper bound | `4*N_i` |
| Two left frontier bitmaps | `2*ceil(N_i/8)` |
| Right mate left ID, predecessor left ID and choice | `10*Q_i` |
| Right visited bitmap | `ceil(Q_i/8)` |
| Field labels for owned cells | `4*C_i*F` |
| Exact sender suppression bitmap | `ceil(q/8)` per machine |
| Queues, tracing, deltas, caches, stacks, metadata, runtime | Explicit additional per-host budgets |

Here `N_i`, `Q_i`, and `C_i` are the actual local request, right-vertex, and cell
counts. Before sender bitmaps and extra buffers, the aggregate is approximately
`18.25*N + 14.125*q` bytes. For the saved `2^29` input this is 16.1875 GiB across
the fleet, or 1.80 GiB per machine with perfectly equal placement. Add 64 MiB
per machine for sender suppression: approximately **1.86 GiB per machine**
before buffers, stacks, allocator overhead, and operating-system headroom.

For an illustrative additional 512 MiB worker budget, the modeled process
allocation would be about 2.36 GiB on an equally loaded machine. An implementation
must itemize that budget, including both directions of every peer queue,
maximum concurrent trace tasks, temporary checkpoint buffers, and actual thread
stacks. Account separately for durable blob storage, disk page cache, and the
coordinator process on its host. Weighting work toward Merlin changes its share.

The comparable current replicated estimates are roughly 11.4 GiB per shard plus
11.7 GiB for its coordinator; see [FULL_SCALE.md](../matching_solver/FULL_SCALE.md).
The proposed savings come with the communication and phase-count risks above.
Admission must cover the maximum of field build, search, commit, checkpoint,
restore, and verification allocations, including any overlapping lifetimes.
Do not authorize a larger production field from this spreadsheet alone.

## 7. Keeping machines and cores useful

Distribute field generation and local sorting across all granted cores. During
search, use dynamic bounded tasks for `(left, choice-range)`, packet processing,
and path tracing. Split long adjacency lists so a narrow frontier does not force
one thread to scan every edge. Separate network progress from compute tasks so
a blocked scan cannot stop inbound messages needed to unblock peers.

Balance virtual cell partitions by measured scan cost and owned state; balance
right partitions by discovery traffic as well as vertex count. Change ownership
only at a quiescent checkpoint/epoch boundary in the first version. Adding a
machine means restoring or repartitioning at that boundary, not moving mutable
vertices during BFS. Membership changes invalidate all old routing generations.

Later, permit idle machines to scan leased read-only cell/choice chunks for an
owner. Helpers need bounded field caches and snapshot descriptors, return
candidates to the owning protocol, and never commit matching state. Tag and
count helper tasks in barriers. Benchmark the added field/candidate transfers
before enabling this across machines.

One matching cannot guarantee useful CPU work on every core at every moment:
late searches can have narrow frontiers, hot right owners, or network barriers.
Use fleet throughput as the objective. Eventually allow unused compute grants
to run independent DP tiles or other matching jobs while reserving enough CPU
and RAM for ownership/network services. The current exclusive group reservation
does not provide that elastic behavior; it needs a generic lease extension,
explicit worker quiescence, and atomic CPU/memory accounting before sharing.
Until then, compare smaller matching groups against full-fleet groups and the
opportunity cost of delaying DP.

## 8. Durable recovery and portable checkpoints

Separate a completed in-memory matching commit from a durable checkpoint.
Between checkpoints, failure may lose several augmentation batches. Report both
commit and durable-checkpoint numbers, durable cardinality, and checkpoint age.
Checkpoint on a configured time/work cadence and on orderly pause, at completed
commit boundaries. A long BFS may delay that boundary; the first version must
expose this limitation. Resumable search-frontier snapshots are a later feature.

At a checkpoint barrier, owners freeze the same committed epoch and write
immutable assignment chunks. Use a new versioned partition-checkpoint manifest;
do not label it KHS1 unless it is byte-compatible with that format. Include:

- exact DP digest, polynomial, graph/ownership-enumeration version and dimensions;
- matching epoch, cardinality, coverage of canonical left IDs, chunk hashes,
  byte counts, and committed counter values;
- virtual partition descriptors independent of physical hostnames.

Each chunk must have at least two validated replicas on distinct live machines
before publishing the manifest as durable. Publish the complete manifest and
then atomically advance the latest-durable pointer; retain earlier valid
generations. A partial checkpoint cannot replace the previous one. Immutable
field-block caches are independently reusable even if a matching checkpoint
does not finish.

Store left assignments and choices; reconstruct right ownership on restore.
With implied left IDs and fixed-width `(right,choice)` records, this is about
`6*N` bytes, or 3 GiB before headers at `2^29`. Replicas, retained generations,
transient files and metadata are extra disk use. Stable virtual partitions allow
the same chunks to move to different machines; a changed partition scheme needs
a bounded streaming redistribution of canonical IDs.

Restore validates the complete manifest, streams each chosen edge to the new
left owner, checks it against the field, and routes assignments to right owners
to reject duplicate occupancy. Recompute BFS scratch after restoration. Fence
the old group before granting replacement ownership. If a checkpoint is missing
or damaged, use the latest earlier complete valid generation, not a mixture of
chunks from different matching epochs.

Provide streaming import of the current KHS1 committed assignments so an existing
partial matching can seed this engine. Validate its graph identity and every
edge; import does not copy old search state. Export ordinary KHM1 in canonical
left order through bounded merge/packing buffers. Cell ownership is not canonical
left order, so this merge is an explicit component, not a giant coordinator array.

## 9. Verification, interfaces, and proposed code boundaries

Keep the solver's ownership protocol independent of King Hamming arithmetic.
A graph provider exposes dimensions, canonical request decoding, partition cost
estimation, owned immutable adjacency construction, and bounded neighbor scans.
Use explicit tiny graph fixtures for protocol tests before attaching the field
provider. Reuse existing arithmetic and codecs through small library interfaces,
without importing the current replicated solver's state structures.

Proposed C modules are graph/field provider, ownership map, owner state, batched
BFS, path/commit protocol, bounded transport, checkpoint codec, certificate
export, and telemetry. The Python cluster adapter handles trusted launch and
artifact lifecycle through [the adapter contract](../cluster/ADAPTER_GUIDE.md).
Peer channels and elastic CPU grants belong in the generic cluster runtime;
field semantics and matching policy do not.

For small and medium cases, export KHM1 and run the existing independent
matching verifier, plus rendered-row checks where affordable. For large fields,
verification itself needs a measured memory/time plan: the existing field-based
verifier can be smaller than the matcher, but is not automatically fast. A future
distributed verifier must independently check selected edges and right-vertex
uniqueness; merely checking the solver's pair arrays is insufficient.

The result's permutation-array size is `theta*F*F + q` rows, each of length
`q+1`. Never confuse row count with total cells. Matching verification concerns
N chosen edges; literal construction and all-pairs row checking can be vastly
larger and are only small-instance sanity checks.

## 10. Delivery stages and acceptance gates

1. **Ownership and graph provider.** Exhaustively compare request IDs, choices,
   sorted cell contents and neighbor labels with the existing implementation on
   small fields. Test uneven partitions and zero-work owners. Instrument every
   allocation to reject an accidental full field or matching copy.
2. **Exact forest oracle.** Run one and several local processes on explicit
   graphs. Compare maximum cardinality against exhaustive small-graph search.
   Include long alternating paths, competing roots, disconnected components,
   perfect matchings and Hall-deficient sets. Check the forest proof invariants.
3. **Transport and commits.** Exercise reordered cross-peer traffic, duplicates,
   tiny queue credits, delayed level markers, unequal core counts and long paths.
   Kill participants before/after prepare, during install, and during checkpoint
   publication. Prove that retries cannot install stale state or report a false
   obstruction. Test sender-dedup equivalence with dedup disabled.
4. **Portable artifacts and recovery.** Import KHS1, export verified KHM1, pause
   and restore with different machine counts and ownership weights. Corrupt or
   remove a replica and test fallback to an earlier complete checkpoint.
5. **Measure on the real network.** Compare replicated and partitioned engines
   on identical DP/polynomial inputs at 2, 4 and 9 machines. Retain peak RSS by
   phase, aggregate bytes, throughput, cardinality gained per scan/phase,
   owner imbalance, barrier time, recovery cost, and independent verification.
6. **Decide the next algorithm.** If repeated forest scans dominate, implement
   actual shortest-path blocking phases under the same ownership contract. If
   traffic dominates, improve exact dedup/locality or reject that target on this
   network. Test these decisions on measured evidence before a `2^29` trial.

No new solver deployment, queue submission, limit increase, or change to the
running campaign is part of this design document. Its first implementation can
be developed and evaluated independently while retained calculations continue.

An isolated native prototype now lives in
[`matching_solver_multi/`](../matching_solver_multi/README.md). It implements
owned field construction and augmenting forests. The managed adapter now adds
lease fencing and durable fixed-owner KMP1 phase recovery; topology-changing
restore and the broader epoch protocol above remain future design work.
Consult its experiment report before assuming ownership partitioning improves
runtime: reducing replicated memory introduces network/barrier costs.
