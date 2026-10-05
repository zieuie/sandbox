# Partitioned matching: first physical-cluster experiments

> **Status (2026-10-04).** This solver stayed an experiment; the campaign matches large fields on GPUs ([../docs/GPU.md](../docs/GPU.md)).

2026-09-30. **Verdict: promising as a capacity experiment, not a replacement for
the existing solver.** Ownership really is partitioned and its certificates
verify, but the first straightforward implementation spends substantial time
synchronizing. Larger batches reduce that cost at the expense of buffer memory.

## Setup and evidence

- Two reserved machines: `192.168.4.107` and `.108`, each an i7-7700T with four
  physical cores / eight logical CPUs. Their campaign agents retained storage
  service; the other seven machines continued the campaign.
- Same DP input and primitive polynomial for both engines. Existing matching
  sources compiled unchanged into isolated, instrumented baseline executables.
- One thread per host for the repeated comparisons; two repetitions, alternating
  engine order. The eight-thread large-batch experiment is a single sample.
- Native C matching in both implementations. The baseline coordinator and test
  driver ran on Merlin with affinity `0,8`, outside its campaign CPU allocation.
- Every reported completed run produced a full matching accepted by the existing
  independent KHM1 verifier. Nine additional local correctness cases passed,
  including tiny-graph cardinality checks against a separate oracle and rejection
  of corrupted certificates. Obstruction/recovery/failure injection coverage is
  not established by those successful full-matching runs.

Raw logs, certificates, per-owner timings and comparison JSON are retained under
`matching_solver_multi/experiments/` (ignored generated artifacts). The source
benchmark driver and this report are the reproducible, versionable record.

## Measured results

Times include process startup and certificate creation/download; exclude binary
staging and independent verification. Repeated entries use median wall time and
median summed native peak RSS. MiB means 1,048,576 bytes.

| Field | Threads/host | Engine | Batch | Solve seconds | Native peak sum, MiB | Application sent, MiB |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| 7⁵ | 1 | Existing replicated | existing policy | 1.94 | 22.5 | 3.27 |
| 7⁵ | 1 | Partitioned | 65,536 | 4.10 | 6.71 | 6.27 |
| 13⁵ | 1 | Existing replicated | existing policy | 45.03 | 39.4 | 132.85 |
| 13⁵ | 1 | Partitioned | 65,536 | 162.46 | 31.1 | 429.20 |
| 13⁵ | 1 | Partitioned (single sample) | 1,048,576 | 44.87 | 59.8 | 360.08 |
| 13⁵ | 8 | Existing replicated | existing policy | 27.12 | 38.5 | 131.38 |
| 13⁵ | 8 | Partitioned | 1,048,576 | 43.63 | 59.5 | 360.81 |

The native peak sum is **not total cluster memory**: it adds separately observed
component high-water marks, which need not coincide, and excludes Python,
verification, SSH and storage agents. The baseline includes its native coordinator;
the partitioned prototype has no native coordinator. Small-process high-water
marks can include inherited pre-exec memory. Thus the dramatic small-field RSS
ratio is not a reliable extrapolation of large-field memory savings.

Application byte counters exclude TCP/SSH framing, staging and certificate
downloads. Baseline I/O instrumentation adds an atomic counter per transport
read/write; it is not a hardware network measurement. More detailed definitions
and commands are in [README.md](README.md).

### What the larger repeated case tells us

At 13⁵, with one thread and small batches, partitioning was about **3.6× slower**,
with about **21% lower native peak sum** and **3.2× application traffic**. Its
52 augmenting phases scanned 1.256 billion edges, versus 50 phases / 1.539 billion
for the existing engine: fewer scans alone did not mean a faster solve.

One owner reported 125.52 seconds in exchange calls out of 159.26 seconds of
search. Exchange timing includes peer wait, scheduling and protocol overhead;
it does **not** establish that the link bandwidth was saturated.

The eight-thread, large-batch sample was still about **1.6× slower** than the
eight-thread baseline and used a larger native peak sum. Batch size and thread
count changed together, so that comparison alone cannot establish thread scaling.
Scheduling/batch changes also affect tie-breaking and phase counts, while
independent verification checks the resulting certificate rather than identity
with a previous matching.

A final controlled-batch check used one thread with the same 1,048,576 batch:
**44.87 seconds**, versus **43.63 seconds** with eight threads. These single
samples show little multicore benefit on this case, and point to batching as the
main source of the improvement over 162 seconds. With large batches, one thread
approximately matched the existing one-thread runtime but used more measured
native memory. This is not evidence of good all-core utilization.

## Capacity is still the reason to pursue this

The prototype's principal state is approximately `48*q/owners + q/8` bytes per
balanced owner when the left request count is about `q`, plus cell positions,
buffers and runtime overhead. At `q = 2^29` and nine owners, that is roughly
**2.73 GiB per owner before those extras**. This is a layout calculation, not a
measured admission estimate or proof that a large solve will finish promptly.
The prototype uses a less compact layout than the design document's target.

No large-field run was submitted and no campaign memory/field limit was raised.
Small fields remain better served by the existing solver. The opportunity is to
solve fields whose replicated state does not fit, after reducing synchronization
and establishing recovery and bounded resource admission.

## Recommended next work

1. Separate scan scheduling from communication flushes: avoid a global reduction
   and fresh I/O threads for every small edge chunk. Use persistent transport
   workers, coalesce messages, and measure overlap without breaking BFS barriers.
2. Decouple buffer capacity from scan chunk size; retain large scans without
   reserving equally large message arrays. Track maximum live buffers and actual
   per-phase RSS before claiming a safe memory budget.
3. Measure the shared atomic discovery bitmap's contention. Compare exact
   thread-local suppression or partitioned discovery ownership with a bounded RAM
   budget; never substitute a lossy probabilistic filter.
4. Improve path/phase efficiency only after transport measurements, then repeat
   with larger fields and 2/4/9 owners. Current tiny-field timings cannot predict
   days-long fields or full-cluster scaling.
5. Add the design's versioned commit/checkpoint protocol, failure injection,
   recovery, resource admission and scheduler adapter before any production use.

The prototype remains separate under `matching_solver_multi/`; none of the
campaign's solver binaries were replaced.

After testing, both borrowed agents were restored. The campaign reported running
with a healthy scheduler and all nine nodes healthy in compute+storage mode:
eight nodes allocated 8/8 logical CPUs and Merlin allocated 14/14. The 101³ DP
had 132 durable tiles, nine running and zero failed at that check. No experimental
`kh_match_multi` processes remained on either borrowed host.
