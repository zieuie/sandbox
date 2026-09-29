
## Requirements

1. **Faithful mathematics.** Match the algorithm in the paper exactly. Identify
   each stage, convention, and permitted choice before optimizing it.
2. **Efficient C implementation.** Reuse lessons from existing attempts; measure
   runtime and memory on representative instances.
3. **Resilient household cluster.** Distribute work across several machines.
   Recover lost work when a node disappears, and provide local detection and
   automatic reboot/recovery for unhealthy nodes.
4. **Fast results first.** Build a large results table, prioritizing entries
   expected to finish fastest so useful results accumulate early.
5. **Central review.** Sync results to a central location for inspection.
6. **Compact, reconstructible results.** Store the prime power, primitive
   polynomial, DP setup, and a small matching certificate instead of the full
   permutation grid. Reconstruct the full matching and output when requested.
7. **Independent verification.** A verification program must check the compact
   artifact and the mathematical claim it makes.
8. **Terse documentation.** Explain each solving method, its assumptions, and
   how to reproduce and verify its outputs without excessive prose.

## Existing work to inspect

- [`hamming/odd.py`](../../hamming/odd.py): historical implementation.
- [`hamming/README.md`](../../hamming/README.md): reconstruction of the construction.
- [`hamming2/odd.py`](../../hamming2/odd.py): newer standalone Python implementation.
- [`hamming2/odd.c`](../../hamming2/odd.c) and
  [`hamming2/odd_peek.c`](../../hamming2/odd_peek.c): existing C attempts.
- [`hamming2/README.md`](../../hamming2/README.md): existing usage notes.

These are references, not the specification. For example, the historical README
equates `27 * (4 + 1)` with `144`; it is `135`. Audit claims against the paper
and implementation rather than inheriting them unchecked.

## Candidate design

### Paper first

Create a short correspondence between paper steps and implementation stages:
inputs, field representation, DP recurrence and reconstruction, graph definition,
matching, and final construction. Record coefficient order, element labels,
slope order, and tie-breaking wherever they affect reconstruction. Establish
small worked examples after the paper arrives. Decide with the author which
optimizations preserve the required algorithm exactly.

### Fast solver

Profile field arithmetic, DP, graph construction, and matching separately.
Candidates include compact contiguous storage, implicit neighbor generation,
reuse of field/DP computations, and avoiding expansion of permutation arrays.
Inspect the existing C field tables for quadratic memory costs before reusing
them. Bound integer sizes and allocation arithmetic explicitly.

Start with independent instances distributed across machines; splitting one
large matching across nodes is a separate question. Threading within a worker
should follow profiling and available memory.

### Scheduling and recovery

A candidate architecture is a durable central job queue with workers that pull
jobs using expiring leases. Heartbeats renew leases; abandoned jobs become
eligible for reassignment. Completion must tolerate duplicate execution and
late submissions without overwriting a valid result.

Estimate remaining runtime and memory using input features and measured runs.
Assign short jobs to machines that can fit them, refining estimates over time.
Count distinct verified table entries as progress, rather than duplicate runs.
Decide whether to reserve any capacity for slow entries to prevent starvation.

Workers keep completed artifacts in a durable local outbox and retry upload
after network or coordinator outages. A result is complete centrally only when
its artifact is durably stored and verified. Checkpoint expensive stages when
the saved recomputation exceeds checkpoint cost; checkpoints carry input and
solver-version identities.

### Node self-recovery

Use progress and agent heartbeats to distinguish a stalled solver, failed agent,
network outage, and missing machine. Restart a stalled solver from checkpoint.
If the agent heartbeat stops, the coordinator or system service restarts the
agent. If that does not restore its heartbeat, durably record the escalation and
invoke the lab.s `restart` shell command over SSH.

Permit at most one automatic machine reboot per incident. If the machine does
not return after the reboot grace period, mark it unavailable and reassign its
lease. Persist the reboot-attempt flag before issuing the command so coordinator
failure cannot cause a reboot loop. Intentional cluster stop suppresses this
escalation, and coordinator/network loss alone never causes a reboot.

A fully frozen or unreachable machine cannot accept an SSH restart. Recovery in
that case means moving its leased work to another node from the latest central
checkpoint; external power recovery remains a manual option.

### Compact artifact and matching certificate

The result is a linked pair of artifacts: a reusable DP artifact and a field/
matching artifact referencing it (see "Separate DP and matching stages" below).
Together their logical contents are:

- Format and mathematical algorithm versions; exact prime power `p^r`.
- Primitive polynomial coefficients and explicit basis/label conventions.
- DP parameters and selected split in the DP artifact; a reference to that
  artifact in the matching result, plus choices needed for reconstruction.
- Matching encoding and witness; any exceptional choices or tie-break data.
- Claimed result, artifact checksum, and solver provenance.

The precise schema depends on the paper. Reconstruction must have stable
semantics rather than relying on incidental behavior of a particular C binary.
A checksum detects corruption; it does not prove mathematical validity.

Matching encodings worth exploring:

| Encoding | Size / reconstruction tradeoff |
| --- | --- |
| Formula or structured family plus exceptions | Potentially tiny and fast, if the graph admits suitable structure. |
| Packed chosen-neighbor index for each left vertex | Concrete baseline; smaller than explicit field labels when degrees are small. |
| Deterministic search recipe plus seed | Potentially tiny, but may repeat expensive matching work; a seed alone is not a cheaply checkable witness. |

Measure certificate bytes and hydration/verification time together. Do not
assume every matching has a tiny, quickly decodable representation. Keep a
general witness encoding available while investigating structure.

### Verification and central results

Prefer a small independent verifier that decodes the artifact, checks field
data and construction constraints, and verifies that matching edges are legal,
right endpoints are distinct, and all required left vertices are matched.
Derive and check the claimed bound using the paper's conditions. Distinguish
validity of a construction from DP optimality or maximum-matching claims: those
stronger claims need their own verification if reported.

Use full permutation expansion and direct checks for small reference cases.
Large cases should be verifiable from the compact representation without
materializing the full grid, subject to the paper's proof obligations.

Store immutable artifacts centrally, with a searchable results index containing
parameters, bound, verification status, timings, peak memory, certificate size,
and provenance. Keep failed attempts distinct from mathematical impossibility.
Back up both the artifacts and the index; choose the central host/service later.

## Next discussion

- Continue the paper/code correspondence audit and review prototype outputs.
- Define one table entry and the desired parameter ranges: prime powers only,
  or multiple polynomials, DP setups, or construction variants per field?
- Clarify what “exactly” fixes, including DP ties and matching choices.
- Set practical targets for certificate size and hydration/verification time.
- Inventory machines, memory, OSes, watchdog support, and central storage.

## Decision log

- Project directory: `king_hamming`.
- Current scope: production solvers live in `dp_solver/` and `matching_solver/`; frozen compatibility fixtures live in `examples/`.
- Mathematical authority: the attached paper and author clarification.
- Distributed architecture, cross-machine matching, checkpoint formats, and operational mechanisms remain proposals.


## Agreed conventions after reading the paper

Source: [prime_power_09_26.pdf](prime_power_09_26.pdf), Section 2 (p. 5),
Table 10, and Lemma 6.

- For degree `r = 2m+1`, write coefficients in descending order
  `(c_2m, ..., c_0)`. Define `prefix(z) = sum(c_m, ..., c_2m) mod p`.
  The prefix contains the highest **m+1** coefficients, including the middle;
  the suffix contains the lowest **m** coefficients `(c_(m-1), ..., c_0)`.
  Do not use the old programs' whole-vector coefficient sum.
- Work in `GF(p)[X]/(f(X))` with a monic primitive polynomial and fix
  `t = X mod f`. Label 0 denotes zero; label `i+1` denotes `t^i` for
  `0 <= i <= q-2`. Thus label 2 denotes X for extension degree greater than
  one. No search for an alternative generator and no separate stored generator.
- Labels are distinct from coefficient values: label 2 does not mean the
  prime-field constant 2 or necessarily `1+1`. Fix coefficient serialization
  order in the format specification as well.

There is no mathematical obstruction from these choices. Prefix and suffix
are additive over GF(p). Fixing both leaves m free coefficients, giving exactly
`p^m` elements per special set; Lemma 6's coverage argument uses this additivity.
The old total residue equals the new prefix residue plus the suffix coefficient
sum modulo p. Consequently old assignments and certificates require rechecking;
identical numerical output or an identical matching should not be presumed.

Primitivity is essential: irreducibility alone gives a field, but powers of X
might not enumerate all nonzero elements. Validate that the polynomial is
primitive and reject unsuitable inputs rather than substituting another
primitive element. Primitive polynomials exist for every prime and positive
degree, so fixing t this way loses no extension-field sizes. If degree-one
fields are added, document their special case: X modulo a linear polynomial
is a constant, and GF(2) has no label 2.

These conventions do not settle matching existence for every DP choice.
Section 5 explicitly says the required matching may not exist in all cases.
Verify each successful instance and distinguish a failed search from a proven
matching obstruction. Architecture and certificate encoding remain proposals;
this update changes documentation only.


## Encountered polynomial-dependent matching failures

Requirement: document every such case encountered during ordinary table
production. Do not run a separate search for failures. The author reports that
changing the primitive polynomial can resolve a matching obstruction.

When a completed exact matching computation cannot saturate the left side,
preserve the failed attempt and try another primitive polynomial for the same
table entry. Hold the DP split, slope-selection rule, and other construction
choices fixed to isolate the polynomial change. Keep `t = X mod f` throughout.
A later success must link back to the failed attempts rather than replace them.
An unresolved case remains recorded even if no successful retry is found.
Retry limits and scheduling remain to be chosen so hard entries do not monopolize
workers or defeat the fast-results-first priority.

For each attempt, record the prime power, full polynomial, exact DP setup and
selected split, construction/label conventions, reproducibility parameters,
solver version, machine, timestamps, runtime, required matching size, attained
matching size, and outcome. Sync these records centrally with the results and
expose a reviewable list of encountered obstructions and their resolution status.
Keep operational outcomes (timeout, crash, resource limit, interruption) separate
from a certified mathematical obstruction.

Agreed requirement: every reported mathematical matching obstruction must include
an independently verified Hall witness, synced centrally with its attempt record.
The witness consists of a set S of left
vertices with fewer neighbors than vertices, `|N(S)| < |S|`. After an exact
maximum matching fails to saturate the left side, alternating reachability from
unmatched left vertices can produce such a witness. Store S compactly and let
an independent verifier reconstruct its complete neighborhood from the graph
specification to check the inequality. A small attained matching by itself does
not prove that a larger matching is impossible. Do not assume Hall witnesses
will always be tiny.

A verified obstruction for polynomial f followed by a verified full matching
for polynomial g, with other construction choices fixed, is a confirmed
polynomial-dependent case. Retain both certificates for comparison. This is
an obstruction to that specified construction graph, not to the finite field
or to every construction at that prime power.


## Operator controls: stop and manual submission

Agreed requirements: one easy cluster-wide stop operation must terminate running
king_hamming calculations without triggering reboot/restart recovery, and an
operator must be able to submit a particular calculation to the queue. These
are design requirements only; the command names below are proposed interfaces.

### Stop, stay stopped, resume

Proposed commands: `kh stop --all`, `kh status`, and `kh resume --all`.
Stop applies to project computation processes, including their child processes;
it leaves the small control agent available for status and resume.

- First persist the cluster's desired state as stopped and stop dispatching work.
  Each node records the intentional-stop state before terminating its workers.
- Request orderly termination with a short deadline, then forcibly terminate
  remaining computation processes. Save a checkpoint if feasible within the
  deadline; stopping must not wait indefinitely for a checkpoint.
- Supervisors treat intentionally stopped workers as healthy inactivity and do
  not restart them or reboot the machine because computation has stopped. Keep
  hardware watchdog servicing in the control/supervision layer, independent of
  solver progress, so stopping workers does not itself cause a watchdog reset.
  Genuine OS/hardware failures remain a separate recovery condition.
- Preserve completed results and queue entries. Record interrupted attempts as
  operator-stopped, with unfinished work held for explicit resume. Late results
  may be stored and verified without restarting computation or clearing stop.
- Persist stop state across coordinator and node restarts. A worker must obtain
  fresh permission before launching work; stale queued commands cannot override
  a newer stop. Use a control generation or equivalent ordering mechanism.
- Show acknowledged, pending, and unreachable nodes. No network command can
  immediately stop an unreachable machine. Require renewable permission to
  continue computation: a responsive isolated node stops workers when that
  permission expires, without rebooting for lost connectivity. A fully hung
  machine cannot promise prompt process termination. Never report all nodes
  stopped without the necessary acknowledgments.

This lease policy trades continued computation during an outage for bounded
stop delay on responsive isolated nodes. The timeout remains to be chosen.
Completed artifacts still use the durable outbox and sync when connectivity
returns. An optional drain mode could finish current jobs before stopping;
its semantics must be distinct from immediate stop.

### Manually submit a calculation

Proposed interface: `kh enqueue --spec calculation.json`, returning a job ID.
The specification identifies the prime power, DP setup, and optional exact
primitive polynomial and other construction choices needed to select the task.
Validate the specification before accepting it, including the agreed field
conventions. Manual submissions use the same verification and artifact pipeline
as automatically generated jobs.

Default to the normal fast-results-first policy; offer an explicit priority
option for calculations the operator wants next. Priority takes effect when a
suitable worker is available, respects memory constraints, and does not imply
preemption. Submission while stopped adds a queued job without resuming workers.

Use a canonical calculation identity to detect equivalent queued, running, or
completed jobs and return the existing job/result by default. Permit an explicit
rerun that preserves earlier attempts. If the operator pins a polynomial, keep
that exact calculation's outcome intact; any retry with another polynomial must
be a linked, separately identified attempt rather than silently changing the
requested calculation. Record origin (manual/automatic), priority, and retry
policy for review.


## Encapsulation and manual reuse

Agreed requirement: components must be easy for the author to read, modify,
run manually, and reuse independently. Efficiency must coexist with clear
boundaries and documented interfaces.

Proposed component boundaries:

| Component | Responsibility |
| --- | --- |
| Field arithmetic | Validate primitive polynomials, convert labels/coefficients, perform field operations with fixed t = X. |
| Construction specification and DP | Describe an instance, compute a split, accept and validate an explicitly supplied split. |
| Construction graph | Define vertices and enumerate neighbors from a field and split. |
| Matching | Consume a graph interface and produce a matching or an obstruction witness. |
| Artifact encoding and hydration | Encode/decode certificates and reconstruct matchings or permutation rows. |
| Independent verification | Check specifications, successful constructions, and Hall witnesses without trusting solver conclusions. |
| Queue and scheduling | Manage identities, priorities, leases, retries, and attempt history. |
| Node agent and supervision | Launch/stop computations, report health, and manage recovery. |
| Storage and review | Sync immutable artifacts and expose results and encountered failures. |

The mathematical core should be a C library with explicit inputs, outputs,
resource ownership, and error reporting. It must not require a coordinator,
network access, a background service, or machine reboot privileges. Keep
process lifecycle and operational policy outside the library; avoid hidden
mutable global state or library functions that terminate the host process.

Provide thin command-line entry points for the meaningful stages: field
inspection, DP planning, solving a specified instance, certificate verification,
and hydration. A single calculation must run locally from a file and write
local artifacts. Support composing stages through documented, versioned files
or streams so a manually chosen DP split or alternative matcher can be used
without modifying the scheduler. Do not require materializing a huge graph
just to cross a module boundary; an explicit neighbor iterator can preserve
both encapsulation and efficiency.

For each component, document its purpose, input/output conventions, assumptions,
public interface, and a minimal standalone example. Distinguish stable public
interfaces from private implementation details. The verifier must be separately
runnable and must not merely call the solver and accept its result; document
any low-level code shared with it and the resulting common failure risks.

These are proposed boundaries, not a commitment to a separate executable or
service for every module. Prefer a small, navigable codebase with thin wrappers
and optimize behind the interfaces where profiling justifies it.


## Separate DP and matching stages

Agreed requirement: record DP optimization and bipartite matching as two distinct
stages with separate durable artifacts. A matching job must load a previously
computed DP split without rerunning the optimizer. Polynomial retries reuse
that same artifact. The paper's DP depends on the prime and exponent/budgets,
not on which primitive polynomial represents the field.

### Stage 1: compressed DP artifact

Store the exact DP inputs (prime power, budgets, allowed transitions and any
other recurrence parameters), recurrence/format version, optimum value, and the
selected ordered sequence of `(a, b, t)` triples. Record the tie-breaking rule
for reproducibility. The normal artifact contains the optimal split and its
value, not the entire DP work table; optional diagnostic tables are separate.

Use compact integer encoding and, where useful, run lengths for consecutive
identical triples. Preserve order: sorting or merging nonconsecutive triples
can change the downstream construction. The exact binary codec remains open;
provide a human-readable inspection/export command regardless of codec.
Give the artifact a stable content identity and sync it centrally immediately
when DP finishes, even if matching has not started or eventually fails.

Checking feasibility and the value of the stored split does not by itself prove
optimality. An independent verifier can rerun the relatively cheap recurrence
to check the claimed optimum without requiring matching jobs to rerun it.
Keep manually supplied feasible splits distinguishable from verified optimal
DP results, using the same downstream interface where possible.

### Stage 2: field and matching artifact

Input: an existing DP artifact plus the chosen primitive polynomial and any
explicit construction options. Check input compatibility, build the graph,
and solve the matching. On success, write only the field specification and
compressed matching witness, together with the DP artifact's content reference,
necessary reconstruction metadata, claimed result, and provenance. Do not
copy the DP split or expanded permutation array into each matching artifact.

On a certified obstruction, save the Hall witness and field/DP reference in a
failure artifact instead, following the encountered-failure requirements above.
Each different polynomial has a distinct attempt linked to the same DP artifact.
Restarting stage 2 from a DP artifact is distinct from resuming a partially
completed matching: the latter requires a separate optional matching checkpoint.

Verification and hydration resolve both files and check their identities. A
missing DP dependency must be reported explicitly. Central sync must retain
referenced DP artifacts; a portable export bundles both files so reconstruction
does not depend on access to the original coordinator.

### Queue and review behavior

Represent matching jobs as dependents of a completed DP artifact. Deduplicate
identical DP requests and allow local/manual execution of either stage. Report
DP completion, matching progress, and verified construction as separate statuses
and table columns, with separate timings and artifact links. A DP optimum is a
predicted construction value until the required matching and construction checks
succeed; never label a DP-only entry as a verified permutation-array result.

Illustrative interfaces, not implemented commands:

```sh
kh dp --spec calculation.json --output split.khdp
kh match --dp split.khdp --field field.json --output result.khmatch
kh verify --dp split.khdp --matching result.khmatch
```

This two-artifact design supersedes the earlier tentative suggestion of storing
DP setup inline in every successful matching result.
