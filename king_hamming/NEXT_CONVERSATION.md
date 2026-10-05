# King Hamming: next milestones

> **Historical (2026-09-29).** A handoff written before the GPU solvers, the wired network and the 13⁹ matching. Most milestones below were completed or superseded. For the current state see [README.md](README.md), [results.md](results.md) and [docs/CONTINUOUS_CAMPAIGN.md](docs/CONTINUOUS_CAMPAIGN.md).

Prepared 2026-09-29 after the leader/worker/feeder rollout. This is a fresh
handoff for the next conversation, not a history of the project.

## Current position

- The continuous DP → matching campaign is running on nine machines.
- All deployed agents have the same versioned runtime bundle and advertise
  CPU topology and memory.
- Matching uses pinned native threads and configurable 2/4/full-fleet groups.
- The feeder retains DP results even when matching admission rejects them.
- Multiple independent DP roots may be admitted when the dependency-ready
  tile pool is shallow.
- Distributed-DP resume, attempt lineage, matching retries, resource-aware
  placement, bounded wire I/O, and detailed status telemetry are implemented.
- The latest rollout exposed and repaired two lifecycle bugs:
  stop-time SIGTERM wrapped by a tile helper is now requeued, and retained
  artifacts needed by waiting roots are prioritized during revalidation.
- Replica revalidation continues safely after an agent restart, but its current
  one-object-at-a-time protocol is an important remaining bottleneck.

The broad goal is not merely “all CPUs busy.” It is to keep every machine doing
useful work whenever independent ready work exists, without oversubscribing RAM,
duplicating durable work, starving large matching groups, or weakening recovery.

## Implementation update (local tree, not deployed)

The 2026-09-29 follow-up sweep implemented and locally verified the following:

- typed supervisor outcomes (`intentional_stop`, `stop_failure`, and
  `engine_failure`) now drive queue transitions; diagnostic-string recognition
  remains only as a one-time migration for rows written by old agents;
- feeder attempt selection prefers completed results, then the live attempt with
  the most durable tile/checkpoint progress, rather than list position alone;
- adapter-owned status augmentation reports durable/running/ready/blocked/failed
  DP tiles and the first missing dependency boundary;
- idle nodes carry a concrete scheduler/storage/admission reason;
- agents periodically sample whole local process groups, including remote matching
  shards, and the leader retains a seven-day utilization time series;
- schema version 4 is advertised, and guarded worker upgrades write an atomic
  `last_rollout.json` through every replacement stage, including recovery advice;
- checkpoint garbage collection is deferred while a replacement agent is proving
  its retained-storage ownership;
- each storage tree has a durable generation UUID; replacement agents validate
  up to 64 retained artifacts/checkpoints per exchange, use metadata checks only
  for the same disk generation, and force full hashes after disk replacement;
- `dp_solver/analyze_templates.py` produces a canonical atom/budget-residue
  inventory, and `matching_solver/analyze_choices.py` measures bounded-prefix
  entropy, delta entropy, and ordinary compression;
- `row_verifier/kh_verify_rows` independently parses KHD1/KHM1, rebuilds the
  quotient field and P/Q sets, renders all covered P&E rows plus the freebie,
  explicitly checks permutations, exhaustively checks pairwise distance, reports
  a closest-pair witness, and optionally publishes human-readable rows.
- the combined real-process fault test now stops a partially durable DP root,
  replaces the leader and every agent, batch-revalidates retained storage,
  resumes the exact frontier, and proves that no tile coordinate was duplicated;
- King-Hamming feeder policy lives in `campaigns/king_hamming.py`; the old
  `cluster/continuous_campaign.py` command is a compatibility shim and deployed
  bundles include the new campaign package;
- feeder ready-work demand scales from the current healthy compute-node count,
  retains configured/hard bounds, and appears explicitly in campaign status;
- `cluster/benchmark_canary.py` records a bounded before/after throughput,
  utilization, memory, tile-progress, and idle-reason report;
- `cluster/resources.py` is the shared typed CPU/memory admission authority and
  exposes a conservative future-slot ceiling without enabling concurrent leases;
- `cluster/ADAPTER_GUIDE.md` documents the trusted solver boundary and points to
  the non-King-Hamming copied-runtime fixture that proves basic reuse.

`make -C cluster check`, `make -C dp_solver check`,
`make -C matching_solver check`, and `make -C row_verifier check` all pass,
including the real distributed replacement/revalidation scenario. None of these
new changes has been deployed to the live nine-node campaign.

Still open from the milestones below are multi-slot host scheduling, deeper DP
template proof/search, structured matching experiments, partitioned-memory
matching for 2^29, finer generic-runtime module separation, and live rollout
validation. Those changes need further design; they were not papered over with
partial unsafe implementations.

## Milestone 1: finish hardening the current rollout

- Verify the retained `53^3` and `83^3` tile frontiers finish revalidation and
  continue without duplicate computation.
- Add a full integration test for: active DP tiles → global stop → agent/leader
  replacement → replica revalidation → resume from the same durable frontier.
- Replace string matching for “failed while stopping” with an explicit typed
  stop/requeue outcome shared by wrappers, agents, and the leader.
- Make feeder retry selection robust when several historical attempts for the
  same field exist; prefer the nonterminal attempt with the most durable work.
- Batch retained-storage inventory/revalidation instead of two HTTP requests and
  a full file hash per object. Preserve safety with a storage-generation identity,
  size/hash sampling or Merkle inventory, and explicit invalidation on disk loss.
- Ensure garbage collection cannot remove an object while its new agent session
  is proving ownership.
- Add rollout failure/rollback reporting: which leader, feeder, and workers were
  replaced; bundle hashes; schema version; inventory progress; and exact recovery
  instructions after a partial rollout.
- Clean up duplicate/cancelled attempt presentation in campaign status.

Done when a stop/upgrade/resume can be exercised repeatedly under load without
losing progress, creating duplicate roots, or waiting through the entire retained
blob inventory before useful work resumes.

## Milestone 2: measure and maximize useful utilization

- Record time-series utilization per node and assigned CPU, not only cumulative
  process CPU and peak RSS.
- Separate idle causes in status:
  - no dependency-ready tile;
  - waiting for artifact replicas;
  - waiting for enough matching partners;
  - memory admission;
  - feeder/backpressure/disk limit;
  - intentionally reserved leader capacity.
- Report ready, blocked, running, and durable tile counts for every active root,
  plus the critical dependency boundary that prevents the next tile.
- Define utilization objectives over useful intervals, for example:
  - at least 90% assigned CPU utilization while enough ready work exists;
  - no healthy node idle for more than one scheduling interval when an admissible
    independent job can run there;
  - bounded time to coalesce the fleet for a high-priority matching group.
- Add a small benchmark/canary command that captures throughput, CPU occupancy,
  memory, network bytes, artifact setup time, and scheduler idle reasons before
  and after each optimization.
- Tune the feeder’s root cap and ready-tile target from measured demand rather
  than fixed guesses. Keep a deeper pool of independent roots when dependency
  waves are narrow, but retain disk and matching-backpressure bounds.

Done when “why is this core idle?” can be answered from one status snapshot and
changes can be compared using repeatable throughput/utilization measurements.

## Milestone 3: resource-aware multi-slot execution

- Generalize one-agent/one-lease scheduling into multiple explicit slots per
  machine. Each slot owns disjoint CPUs, a RAM budget, scratch space, and leases.
- Allow several independent DP tiles or small matching jobs on one host when one
  job cannot use all available resources effectively.
- Keep the existing single persistent process/thread-pool design for a large
  matching shard when that benchmarks better than multiple processes.
- Model physical cores and SMT siblings explicitly. Benchmark four physical cores
  versus eight logical CPUs on the small nodes, and seven versus fourteen on
  Merlin, separately for DP and matching.
- Enforce aggregate host memory, CPU, disk, and network reservations transactionally
  across all slots. Never admit based only on each process’s individual limit.
- Add per-program slot shapes, such as:
  - one full-host matching shard;
  - one medium matching shard plus spare DP capacity when safe;
  - several one- or two-core DP tiles;
  - coordinator capacity separated from worker-shard capacity.
- Preserve fencing and recovery independently per slot: stop, crash, stale PID,
  agent restart, lease expiry, and partial group failure.
- Make matching group placement aware of remaining capacity so two small groups or
  DP work can coexist without blocking a future full-fleet job forever.

Done when independent work can occupy all useful CPUs on every node without CPU
overlap, RAM overcommit, scheduler races, or replicated-memory surprises.

## Milestone 4: DP throughput and dependency efficiency

- Profile the optimized tile kernel to find the next hot operations after cached
  transition deltas/IDs.
- Investigate the very small family of triples appearing in optimal splits.
  The current 57 retained KHD1 artifacts contain only 209 run records total,
  normally 2–6 records per field. Typical families are visibly structured:
  - `2^r`: `(1,1,1)` plus symmetric `(1,2,1)` / `(2,1,1)` counts;
  - `3^r`: symmetric `(1,3,1)` / `(3,1,1)` counts with an occasional
    `(2,2,1)` correction;
  - `5^r`: almost entirely `(2,2,2)` plus three boundary corrections;
  - larger primes: a small mixture of balanced `(a,b,t)` atoms, their swapped
    partners, and a few budget-residue corrections.
- Treat this first as a mathematical/template question, not a format rewrite:
  - tabulate atom types, counts, budget residues, score, and tie-selected order;
  - conjecture a per-prime or per-residue template;
  - prove feasibility and score directly;
  - compare its score and exact tie-selected sequence against the existing DP;
  - search explicitly for the first counterexample over a bounded prime/degree
    range before relying on it.
- Explore replacing the full two-dimensional choice table with a small integer
  mixture/residue problem or closed-form reconstruction if the pattern can be
  proved. This could save orders of magnitude more time and state than another
  few bytes of artifact compression.
- Keep KHD1 as the compatibility baseline. It already run-length encodes
  `(a,b,t,repeat)`: all 57 retained files total 3,223 bytes, including 1,824
  bytes of checksums; only 878 bytes hold run records. A future KHD2 dictionary
  or template ID should be introduced only if it simplifies proofs/decoding or
  materially helps much larger result sets, and must decode canonically to the
  same KHD1 run sequence.
- Benchmark tile side, thread count, slot size, and root interleaving across the
  actual two hardware classes.
- Reduce repeated setup:
  - cache immutable transition tables by field/specification;
  - keep a bounded content-addressed dependency cache;
  - pin in-use cache entries and account for their memory/disk;
  - prefer nodes already holding required predecessor tiles.
- Batch dependency metadata and artifact fetches. Avoid repeated small requests
  and repeated unpack/hash work for the same inputs.
- Investigate scheduling several dependency-ready tiles from different roots per
  host, prioritizing the tiles that unlock the largest next wave.
- Add critical-path/descendant-unlock priority rather than simple row/column order.
- Measure whether tile-local multithreading or more independent single-core tile
  processes gives better throughput and memory behavior.
- Retain deterministic outputs and byte-identical reconstruction across tile sizes,
  worker counts, interruptions, and cache hits.

Done when DP performance scales predictably with added cores/machines and narrow
dependency waves no longer leave most of the fleet idle.

## Milestone 5: matching throughput and larger fields

- Distinguish ordinary certificate compression from a genuinely structured
  matching. Measurements of retained successful KHM1 artifacts show:
  - fixed-width choices for the largest cases use 12 bits per request;
  - choice values over the whole request range are nearly uniform and use every
    one of the `f` neighbor indices, unlike the tiny DP triple vocabulary;
  - choices have useful local/delta structure: sampled sequential delta entropy
    is about 7.5–8.8 bits instead of 12;
  - fast gzip reduces representative 50–94 MiB certificates to about 76–79% of
    their original size;
  - every suffix-block choice vector examined was unique, even after subtracting
    its first choice, for `2^17`, `3^11`, `7^7`, `13^5`, and `29^3`.
- Therefore consider a simple bounded transport/storage compression layer or a
  block-delta KHM2 with restart points, streaming decode, canonical bounds, and
  independent verification. Expect a useful 20–30% reduction, not DP-like
  orders-of-magnitude compression. This does not solve resident matching memory.
- Separately search for a deliberately structured matching. The current
  augmenting-path solver is allowed to return any full matching, so its apparently
  irregular certificate does not prove that a small algebraic one does not exist:
  - group requests by repeated DP atom `(a,b,t)` and normalize coset/field shifts;
  - solve one candidate motif and translate it through repeated cosets;
  - detect endpoint collisions and try a small explicit boundary/correction set;
  - test cyclic, affine, and polynomially indexed choice rules directly;
  - compare multiple primitive polynomials for whether they expose or destroy
    such structure;
  - expand every proposed template to ordinary KHM1 choices and run the existing
    independent verifier before trusting it.
- If a template-plus-exceptions family exists, store its precise versioned rule
  and exceptions, not merely a solver seed. A seed that reruns expensive matching
  is not a cheaply checkable certificate.
- Use the new proposal/BFS telemetry to attribute time to scans, communication,
  barriers, augmentation, checkpointing, and field arithmetic.
- Replace repeated coordinator thread creation/join with persistent bounded I/O
  workers if measurements show meaningful overhead.
- Investigate owned-frontier or chunked BFS so workers scan only relevant ranges
  instead of repeatedly scanning whole local arrays.
- Weight vertex/shard ownership by measured CPU, memory bandwidth, and network
  throughput. Merlin and future machines should receive proportionate work.
- Batch and compress sparse proposal/frontier traffic while keeping bounded memory
  and deterministic conflict resolution.
- Tune 2/4/full-fleet group thresholds empirically and allow concurrent small
  matchings when they improve total throughput.
- Build calibrated memory models from observed RSS instead of conservative limits
  alone; retain headroom and reject unsafe coordinator placement.
- Design truly partitioned field, matching, distance, and predecessor ownership so
  `2^29` does not require every machine to replicate giant logical arrays.
  The separate-solver proposal is now in
  [`docs/PARTITIONED_MATCHING.md`](docs/PARTITIONED_MATCHING.md): owned cells and
  vertex arrays, an exact augmenting-forest baseline, bounded peer traffic,
  portable recovery, and explicit memory/throughput acceptance gates. It remains
  a design; implementation and production admission are future work.
- Define portable partition/checkpoint metadata so a job can resume with a
  different worker count or ownership split.
- Treat out-of-core/random-access storage only as a measured fallback; do not assume
  disk can replace RAM efficiently.

Done when matching scales with heterogeneous machines, small jobs use only the
resources they need, and `2^29` has a tested partitioned-memory execution plan
rather than merely a raised admission limit.

## Milestone 6: independent rendered-row sanity solver

Build a deliberately straightforward verifier that reconstructs the completed
construction, renders the actual rows after Partition and Extension, and checks
their Hamming distance pairwise. This is a sanity oracle, not the fast path.

- Start a small `row_verifier/` directory with one obvious C executable, for
  example: `row_verifier/kh_verify_rows RESULT.khmatch --dp SPLIT.khdp`.
- Resolve and document three scope/convention questions before coding:
  - verify one base P&E construction first, or also materialize every cyclic
    replica/“pi” counted in a larger reported bound;
  - the exact slope/coset order and freebie multiplier used by current artifacts;
  - the deterministic covered-position rule when a row has more than one valid
    `P_i` position whose image lies in `Q_i`.
- Accept only a successful KHD1 + KHM1 pair initially. Check both checksums and
  their reference/hash relationship before rendering anything.
- Keep the C implementation independent of the production DP and matching
  kernels. A tiny documented artifact reader may be shared, but do not call the
  optimized field builder, neighbor generator, matching search, or a production
  row renderer.
- Prefer ordinary C99/C11 and explicit structures over clever packing:
  - checked `uint32_t` labels and `uint64_t` counts;
  - a plainly named `struct field`, `struct block`, and `struct row_origin`;
  - `P` and `Q` blocks as simple counted arrays or visible membership arrays;
  - rendered rows in one checked contiguous `uint32_t` allocation;
  - small functions, single ownership, one cleanup path, and no macros that hide
    mathematical operations;
  - no SIMD, implicit graph scans, cluster code, or production solver shortcuts
    in the reference core.
- Write the mathematical stages as separate short functions whose names mirror
  the paper:
  1. decode and validate the DP split;
  2. decode the matching choices;
  3. reconstruct the finite field and the `P_i`,`Q_i` block pairs;
  4. enumerate each selected affine coset `x ↦ ax+b`;
  5. choose the documented covered position deterministically;
  6. apply the one-symbol Partition-and-Extension move;
  7. append the untouched freebie coset;
  8. validate rows and compare every unordered pair.
- Make the final checks almost pseudocode:

  ```c
  for (uint64_t i = 0; i < row_count; ++i)
      require_permutation(row_at(rows, i), q + 1);

  for (uint64_t right = 0; right < row_count; ++right) {
      for (uint64_t left = 0; left < right; ++left) {
          uint32_t distance = hamming_distance(
              row_at(rows, left), row_at(rows, right), q + 1);
          require(distance >= q);
      }
  }
  ```

- Check more than distance:
  - every row has length `q+1`;
  - every row is a permutation of `0..q`;
  - the rendered row count equals the construction’s claimed bound;
  - rows are unique;
  - every active affine row is actually covered by its assigned `P_i,Q_i`;
  - the freebie rows leave the new symbol in the final position;
  - the minimum observed distance is reported with the responsible row pair.
- On failure, print a compact, reproducible witness: row indices, block/coset,
  multiplier and translation, selected extension position, differing/equal
  columns, actual distance, polynomial, and input artifact hashes.
- Optionally write the fully expanded permutation array as simple text, one row
  per line. Use atomic fresh-file publication and never require this output for
  verification.
- Put hard, explicit limits on `q`, rows, pair comparisons, output bytes, and
  memory. Literal verification costs `O(R²(q+1))` time and `O(R(q+1))` memory for
  `R` rendered rows; refusing an oversized input is preferable to hiding a more
  complicated algorithm in the sanity checker.
- Print the estimated rows, comparisons, bytes, and work before starting. Offer
  `--explain` for a small worked trace and `--render` for the optional row file.
- Do not call sampling “verification.” A future `--sample` diagnostic may exist,
  but its output must be labeled non-exhaustive.
- Freeze tiny hand-checkable fixtures, including at least:
  - one successful construction with every rendered row checked;
  - a deliberately corrupted matching choice;
  - a valid matching whose P&E rendering is deliberately perturbed;
  - duplicate symbol, duplicate row, wrong freebie, and low-distance failures;
  - two primitive polynomials for the same field where conventions are easy to
    confuse.
- Cross-check the simple renderer against historical implementations only as a
  test comparison. The paper and documented current conventions remain the
  authority; historical code is not imported at runtime.
- Keep optimization outside the reference core. A thin pthread driver may split
  disjoint ranges of the outer pairwise loop across cores while calling the same
  obvious `hamming_distance()` function; retain `--threads 1` as the readable
  oracle. If still larger exhaustive cases are valuable, split comparison ranges
  into independent cluster jobs without changing row semantics.
- Consider a compact verification report recording input hashes, row count,
  required and observed minimum distance, closest pair, comparisons performed,
  elapsed time, and verifier version. It is supporting evidence, not a replacement
  for the KHD1/KHM1 artifacts.

Done when a reviewer can read the complete C mathematical path without knowing
the optimized solvers, tiny successful artifacts pass exhaustive rendering,
seeded construction errors produce useful witnesses, threaded and single-threaded
checks agree, and oversized cases refuse before large allocation or work.

## Milestone 7: sharpen abstractions and remove technical debt

- [Done locally] Move `continuous_campaign.py` out of the generic `cluster`
  package into a King-Hamming campaign module; retain the old command as a
  compatibility shim.
- Define a stable solver-adapter interface for:
  - validation and estimates;
  - resource/slot requirements;
  - dependency readiness and critical-path priority;
  - resume/retry transitions;
  - progress/status details;
  - artifact verification and retention.
- Separate the generic runtime into clear modules: queue/leases, placement,
  process supervision, storage/replication, rollout, status, and solver adapters.
- Remove imports from generic cluster code into DP or matching implementation
  details. Registration should supply adapters/plugins explicitly.
- Replace historical launch scripts with thin shims or delete them after proving
  no retained deployment depends on their command/format contracts.
- Consolidate duplicated HTTP, process-identity, atomic-manifest, and artifact
  retrieval logic.
- Add schema migrations with explicit versions and compatibility tests against a
  copied retained database.
- Paginate/filter verbose status; do not print hundreds of healthy historical
  artifacts unless requested.
- [Done locally] Document how another project supplies an adapter and campaign
  policy, then prove reuse with a tiny non-King-Hamming fixture solver.
- Review and commit the large working tree in coherent changesets after the live
  deployment is stable. Do not discard retained deployment state or user changes.

Done when the cluster can plausibly be reused by another solver without importing
King-Hamming modules, and obsolete scripts/files have evidence-backed deletion
boundaries.

## Suggested order

1. Finish rollout/revalidation hardening and the stop/upgrade/resume integration test.
2. Build the small rendered-row verifier as a correctness oracle before further
   solver optimization changes the implementation substantially.
3. Add utilization/idle-reason telemetry and a repeatable benchmark harness.
4. Tune adaptive independent roots using those measurements.
5. Implement CPU/RAM-safe multi-slot agents and benchmark DP process shapes.
6. Optimize DP dependency locality and matching phase bottlenecks.
7. Design and test partitioned-memory matching for `2^29`.
8. Extract reusable runtime modules and remove compatibility shims only after the
   live campaign and retained state no longer need them.

## Guardrails

- Preserve all completed DP and matching artifacts, including currently
  inadmissible matching fields.
- Never raise the 6 GiB matching allocation or 100,000,000-element field limit
  without measured memory evidence and a controlled canary.
- Never interrupt active work for deployment without an explicit stop, verified
  quiescence, and durable-frontier check.
- Keep upgrades race-safe: the database latch must refuse if any run becomes
  active, and success requires the expected bundle version on every node.
- Optimize for useful throughput and recoverability, not cosmetic process counts
  or nominal 100% CPU usage.
- Add machines through explicit inventory and capability checks; do not assume
  homogeneous cores, RAM, storage, or network.

## Useful entry points

- Capacity-first campaign policy: `campaigns/capacity_campaign.py` and
  `docs/CAPACITY_CAMPAIGN.md`. Single-host dispatch and minimum-owner planning
  are tested. Managed partitioned execution now has durable all-owner recovery,
  lease fencing and resource admission. See that document for rollout evidence
  and remaining large-field validation; do not infer deployment from source alone.

- Operations: `docs/CONTINUOUS_CAMPAIGN.md`
- Generic runtime: `cluster/leader.py`, `cluster/agent.py`, `cluster/adapters.py`
- Campaign policy: `campaigns/king_hamming.py`
- Retained command compatibility: `cluster/continuous_campaign.py`
- DP DAG: `dp_solver/adapter.py`, `dp_solver/distributed.py`, `dp_solver/src/dp_tile.c`
- Matching runtime: `matching_solver/adapter.py`, `matching_solver/src/distributed.c`,
  `matching_solver/src/distributed_worker.c`
- Historical row references only: `../hamming/xtar_to_pa.py`,
  `../hamming2/odd.py`; do not import them into the new verifier.
- Rollout: `dp_solver/launch_dp.py`
- Full checks: `make -C dp_solver check`, `make -C matching_solver check`,
  `make -C cluster check`
