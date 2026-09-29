# Exact reduction of DP transitions

For a transition `(a,b,t)`, the two costs are `du=a*t`, `dv=b*t` and the gain
is `t*omega(a,b,t)`. The DP candidate at `(u,v)` is
`value[u-du,v-dv] + gain`, provided both costs are affordable.

## Why identical costs can be grouped

All members of a cost group have the same affordability and predecessor at
every cell. A member with lower gain has a strictly smaller candidate than a
maximum-gain member whenever either is affordable. It cannot be the final
maximizer. Among maximum-gain members, only the earliest in the paper's ascending
`a,b,t` order can be selected by strict improvement.

Keep that earliest maximum-gain member of each group, then sort all retained
members by original `a,b,t` order. The greatest candidate and the earliest
transition attaining it are unchanged. Induction over the DP dependency order
therefore preserves **every value and choice**, including boundary cells and the
ordered split reconstructed from the full-budget cell. This argument makes no
assumption about dominance between different costs.

## Representation and checkpoint compatibility

The encapsulated builder in [`src/transitions.c`](../solver/src/transitions.c) enumerates
all `p^3` 12-byte records, sorts by costs, descending gain and original order,
compacts one winner per group, then restores original order. Sorting avoids a
quadratic pairwise dominance search. There are two library sorts and a linear
compaction pass; enumeration still computes every original omega.

Choice IDs keep their historical encoding:

```text
id = ((a - 1)*p + (b - 1))*p + t
```

Zero remains the empty choice. The DP records this original ID rather than the
position in the reduced array. Reconstruction decodes the ID arithmetically,
so checkpoints from the original solver remain valid. `--raw-transitions` keeps
the unpruned reference array without sorting. Either mode may resume the other;
mode is not checkpoint identity. Artifact encoding is unchanged.

After compaction, an optional `realloc` shrinks the allocation. If shrinking
fails, the larger allocation is retained and reported accurately. Profiling and
benchmark records distinguish the retained scan payload, allocated array bytes,
and peak original array payload. These counts exclude allocator overhead,
the omega bitset and library-sort workspace; they are **not peak process RSS**.
The dense DP files, worker stacks and resident page cache are separate costs.

## Profiling and measurements

```sh
./kh_estimate 11 5 --profile-transitions --json
./benchmark.py --case 11:5 --threads 1 --verify-with-raw --raw-transitions
./benchmark.py --case 11:5 --threads 1 --verify-with-raw
```

Profiling is explicit and defaults to a maximum of 100,000 raw transitions.
Override with `--max-profile-transitions N` after considering enumeration cost.
The ordinary estimator remains fast for all representable prime powers and
reports the conservative raw visit bound. The solver's `--max-visits` admission
also retains that bound; pruning does not silently admit previously refused jobs.

Benchmarks record build time, time spent evaluating DP tiles, time spent flushing
completed tiles and checkpoint metadata, total process elapsed time, and array
payload bytes. Evaluation includes worker synchronization and mapped-page
faults. Initial file creation and the initial empty-state checkpoint are part of
total elapsed time, outside the reported evaluation and tile-checkpoint phases.
Verification runs after the timed process. Default verification recomputes the
DP independently in Python. `--verify-with-raw` instead compares complete value
and choice files and artifact bytes with a separate run of the other scan mode;
this is useful beyond the Python verifier's work limit, but shares the C
recurrence implementation.

Independent cost-group counts:

| p | Raw transitions | Retained | Removed |
|---|---:|---:|---:|
| 3 | 27 | 24 | 11.11% |
| 5 | 125 | 110 | 12.00% |
| 7 | 343 | 295 | 13.99% |
| 11 | 1,331 | 1,119 | 15.93% |
| 17 | 4,913 | 4,050 | 17.57% |
| 31 | 29,791 | 24,179 | 18.84% |

This removes a modest fraction of scans and transition memory. Dense-state size
and the cubic enumeration count are unchanged; it does not make the largest
supported fields feasible by itself.

Local measurements are saved in
[`benchmarks/transition_reduction.jsonl`](../solver/benchmarks/transition_reduction.jsonl).
Each mode has three samples per worker count on the local host (`uther`, the
leader hardware currently also called merlin). Every sample compares full state
and artifact bytes with the alternate mode, obtaining theta=4,181.

| Workers | Raw evaluation median | Reduced evaluation median | Time saved |
|---|---:|---:|---:|
| 1 | 3.031 s | 2.643 s | 12.8% |
| 4 | 0.837 s | 0.733 s | 12.4% |

For p=11, transition array payload falls from 15,972 to 13,428 bytes. Build
time is below one millisecond in these samples; checkpoint flushing is recorded
separately. These are local smoke measurements, not a cluster scheduling model.
Thread barriers, CPU frequency and other workloads can affect elapsed time.
Timing collection did not overlap the test suites.

## Checks

`make check` directly compares each retained record against an independent,
exhaustive group-winner selection for small primes. Python residue sets verify
profile counts, including equal-gain and lower-gain removals. Complete arrays and
artifacts are compared between raw and reduced scans for 2^3, 3^5, 5^3, 7^3,
7^5, 11^3 and 17^3 with different tiles and workers. Tests stop after two tiles
and resume in both mode directions. The independent Python DP and preserved POC
supply further checks for feasible cases. Existing signal, poisoned replay,
affinity and cluster supervision tests continue to apply.
