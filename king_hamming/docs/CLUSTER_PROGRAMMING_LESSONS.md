# Cluster programming lessons from King Hamming

This is a reusable design guide, not a description of one particular campaign's
current state. It records what building, operating, breaking, and repairing the
King Hamming cluster taught us. The examples are real; the numbered constants
are **examples to remeasure**, not defaults for the next project. Historical
notes elsewhere in this repository can describe older deployments, so use the
code and live status when making an operational decision.

The shortest version: design the failure and recovery protocol before scaling
out the computation; make ownership explicit and fenced; distinguish computed
from durable work; keep bulk data off the control plane; measure the actual
bottleneck; and make a rollout as testable as a solver.

## 1. Define the invariants before the scheduler

A cluster is a state machine that happens to use many computers. Write down
what must remain true across a timeout, duplicate request, process crash,
machine reboot, network partition, and operator restart. For this project the
most useful invariants were:

- At most one **current owner** may publish progress or a result for a run.
  Old processes may still be alive, but their lease tokens cannot mutate the
  run after reassignment.
- Bytes are not an artifact until their size and hash are verified and their
  publication is durable. A file being copied, or a database row claiming a
  copy, is not enough.
- A dependency is usable only if its required content is currently available
  from live, verified owners. A historical `complete` flag is insufficient.
- A retry preserves every valid completed unit. A failed final assembly does
  not invalidate thousands of successful child computations.
- Resource exhaustion, missing input, transport failure, operator stop, and a
  mathematical negative result are distinct outcomes.
- Cleanup cannot remove the last usable source of data on which queued or
  running work depends.

Make these invariants executable: database constraints, fenced mutations,
idempotent requests, assertions, and fault-injection tests. A status page can
explain an invariant; it cannot enforce it.

### Keep four identities separate

Do not use one ID to mean all of these:

| Identity | What it means | Why it matters |
| --- | --- | --- |
| Calculation | Canonical mathematical or business request | Deduplicate equivalent submissions and reuse results. |
| Run/attempt | One retained execution history | Preserve failures, timings, and explicit reruns. |
| Lease/token | One temporary right to execute and publish | Fence a late or partitioned former owner. |
| Node session | One incarnation of an agent on a machine | A rebooted agent cannot claim the previous process's work or storage without revalidation. |

Stable content hashes form a fifth identity for immutable bytes. Keep immutable
specifications and attempt histories; do not edit a failed run into a new
mathematical calculation merely to make a dashboard look simpler. A request
should be safely repeatable after its response is lost: check its identity and
return the same outcome or make the next transition atomically.

## 2. Choose work units around *recovery* as well as parallelism

“One job per machine” left most CPU cores idle. Breaking DP into independently
leased tiles made it possible to use more cores and retry only the failed tile.
But tiny units add queue, storage, and network overhead; huge units increase
memory, failure cost, and the time until a durable boundary. The useful size
is the one that balances all four, not the largest object that fits in RAM.

Represent a large calculation as a durable parent with explicit child work:

```text
calculation → retained root attempt → independently leased units → final assembly
```

The root's result is not the sum of its child status strings. It needs a
defined completion condition: all required children complete, required input
copies live, final assembly validated, and output published. Give final
assembly its own phase and scheduling priority. Otherwise a fully computed
field can sit idle behind new tiles, or a network timeout in assembly can cause
an expensive new attempt. We saw both patterns. The eventual 13^9 recovery
reconstructed from its original 8,281 tiles rather than recomputing them.

Retry the smallest failed unit when possible. Put bounded backoff on a tile
that fails transiently; do not cancel its whole root or discard siblings.
Distinguish transport retry from deterministic algorithm failure, and cap the
latter. An automatic retry policy also needs a persistent budget, lest one bad
input loop forever. If a complete grid's reconstruction fails, retry the
reconstruction **in that root** only after checking the original tiles and
their current replicas. A new attempt may still reuse content-addressed tiles,
but it creates lineage and scheduling churn that need not exist.

### DAG width is a real capacity limit

More cores do not create more dependency-ready work. A wavefront can have most
of its tiles complete yet only a narrow edge ready. Requiring durable replicas
before admitting successors narrows it further. The cluster may have 100 free
CPUs and only ten tasks that can safely start. Count `ready`, `running`,
`blocked`, `complete-but-not-durable`, and `failed/retrying` separately. Keep
several independent roots in the queue when the application permits it, so
one root's barrier does not idle the entire fleet. Prioritize reconstruction
over new tiles when it can close a result, but do not starve other roots.

See [queued tiles](QUEUED_TILES.md), [tile design](TILES.md), and the
[reconstruction incident](LEADER_CONTENTION_AND_DP_RECONSTRUCTION_PLAN.md).

## 3. Make the control plane small and the data plane direct

The leader should own metadata: specifications, leases, placement, hashes,
locations, reservations, and state transitions. It should not relay large
arrays or become a file server for every transfer. Workers can copy immutable
objects directly from peers, while the leader authorizes and indexes them.
Transfer in bounded buffers; support resumption; verify exact size and a strong
content hash before publication. Partial files must never be advertised as
complete replicas.

Content-addressed storage paid for itself here. It made retries, replica
repair, cache hits, hard-linked *immutable* checkouts, and artifact reuse
straightforward. But mutable solver state is different: restore it by copying
verified bytes into an isolated lease directory and installing them atomically.
Never hard-link mutable state back into the supposedly immutable store.

Separate the following concepts:

1. **Existence:** an artifact was produced at some point.
2. **Availability:** at least one verified, reachable source exists now.
3. **Durability:** enough independent live copies exist for the chosen failure
   tolerance.
4. **Usefulness:** the artifact lies on a current dependency path or is a
   retained result worth keeping.

Those are not interchangeable database flags. A restarted agent's disk can be
intact while its *claim* is stale; revalidate it under the new node session
before counting it. A timeout does not prove a replica is corrupt. Only a
confirmed missing or hash-mismatched object justifies deleting its claim.

### Replication is a scheduling decision, not just backup

Two live tile copies were required before successors used a tile (later
relaxed to one; see section 13); three was the background target. That protected work, but copying every tile three times
also consumed bandwidth and delayed the dependency frontier. The more urgent
copy is often the one that unlocks a successor, not the oldest object awaiting
its third copy. Give replication explicit priorities, exclusive expiring
transfer claims, and disk admission. Do not assign parallel downloads of the
same object blindly; do not replace a briefly silent node's copy immediately.

Topology matters: keep routine copies inside a fast wired group when it has
enough independent capacity, but retain a reachable fallback across networks.
The control address need not be the fastest data address. King Hamming kept
Wi-Fi addresses for SSH and leader control while workers on the switch
advertised separate private Ethernet blob URLs. A Wi-Fi-only node must never
receive an unreachable private URL. This is more robust than switching the
entire cluster's identity to a new subnet.

### Move the bytes the consumer actually needs

Our early tile workers fetched entire predecessor packets merely to build a
thin boundary. For a measured 13^9 tile, predecessor packets totaled 65.1 MiB
while the necessary edge bands were 1.47 MiB (about 44× less). We added
separately hashed, versioned bands, kept full packets for reconstruction and
fallback, preferred verified local bytes, and shared a bounded per-node cache.
Soft placement affinity helped when a node already held a predecessor; hard
row ownership would have suppressed parallelism. The general technique is to
derive narrow, immutable, verifiable views of large objects, and to keep a
correct full-object fallback while rolling the new format out.

Measure **total** traffic: dependency reads, extra sidecar publication,
replication, retries, and reconstruction. Optimizing one fetch path can simply
move the bottleneck. The measurements and compatibility rules are in
[DP network locality](DP_NETWORK_LOCALITY.md).

## 4. Capacity accounting needs more than CPU count

The admission decision should be a checked estimate of the *whole lease* on
the target host: CPUs, aggregate resident memory, temporary buffers, local
disk, GPU memory and exclusivity, and any partner reservations. A 2 GiB tile
limit does not mean eight such tiles fit on a 16 GiB machine once the OS,
agent, caches, halos, and transfers are included. Likewise, a field's total
DP state size is not the same as the peak RAM or disk required by one tile.
Present these quantities with different names in status and admission errors.

Track disjoint CPU sets and memory **atomically with the lease**. A reported
theoretical slot count is not permission to oversubscribe: the scheduler,
scratch ownership, and fencing must all understand concurrent slots first.
Reserve the coordinator's physical core and its SMT sibling if a leader shares
a machine with workers. Use affinity deliberately. Native threads can run on
different cores; pinning each compute thread to a distinct CPU avoids migration
when that helps, while shared read-only arrays avoid duplicating a huge field
in one process per core. Independent tiles often benefit from separate
processes instead. The choice is about memory ownership, isolation, and the
kernel's measured scaling—not a blanket rule that processes beat threads.

Make every integer and byte calculation checked. Mathematical representability
does not imply operational feasibility: a 64-bit field size may still require
an impossible number of transitions, bytes, or network transfers. Keep wire
format bounds, array-index bounds, and admission bounds distinct. Keep a DP
result even if the current matching engine cannot admit it; one stage's
resource limit should not erase useful upstream work. See the
[resource model](RESOURCE_MODEL.md) and [adapter contract](../cluster/ADAPTER_GUIDE.md).

### Throughput is a vector, not one utilization percentage

Report allocated CPUs, measured CPU utilization, ready-task count, GPU use,
network bytes, disk free, replica backlog, and scheduler delay separately.
“8/8 allocated” means reservations, not 100% computing. A process may be
waiting for a dependency, a GPU lock, an input transfer, a checkpoint, or a
group lease. A long GPU lock held by another task should not make a CPU tile
wait minutes merely to fall back to CPU. Admission and priority policies need
to include these waiting costs.

Compare changes by **completed useful results per wall-clock time**, not only
kernel microbenchmarks or instantaneous CPU load. A faster tile kernel can
produce more copies for a saturated network or leader to manage.

## 5. A single metadata database can be enough—until its writes dominate

SQLite with WAL gave us a durable, inspectable control plane and transactional
fencing without operating a separate database service. It was a good starting
point, not an exemption from contention. Under load, short leases, progress
reports, replica assignment, revalidation, GC, and DAG scans all wanted the
same writer lock. At one point over 100 leader threads accumulated around
SQLite busy waits; a healthy scheduler indicator concealed worker request
failures. Raising a timeout alone would merely hide saturation and retain
more blocked sockets.

The useful sequence was:

1. Instrument per-route **time waiting for** `BEGIN IMMEDIATE`, **time holding**
   the transaction, max latency, and lock errors. Health must include worker
   request failures, not just the background scheduler's last tick.
2. Keep read-only descriptor requests out of the writer queue. Do not set WAL
   mode on every connection. Keep lease fencing checks in the appropriate
   transaction, including local expiry checks where a read route needs them.
3. Make large workflow scans and retirement batches bounded. Fairly rotate
   roots; do not scan every 40,000-tile DAG inside every one-second writer
   transaction. Use a cadence appropriate to task duration.
4. Search expensive replica candidates in a reader, then briefly recheck and
   reserve the selected object under the writer lock. A read/write split needs
   a race-proof final claim, not blind trust in the earlier suggestion.
5. Skip expensive work when its input set is empty, such as building a huge
   protected-artifact set for a GC plan with no pending garbage.
6. Reduce chatty control requests in proportion to the actual lease duration.
   For this campaign, five-second renewals retained generous slack for a
   60-second lease without one SQLite write per busy core per second.
7. **Queue writers yourself.** SQLite doesn't queue waiting writers: each one
   sleeps and retries, backing off to 100 ms between tries. At about 30 writes
   a second an unlucky request could lose the race for more than its 8-second
   timeout although no transaction held the lock for more than a few seconds,
   so a handful of requests a day still failed with `database is locked`. All
   leader writers are threads of one process, so a first-come, first-served
   queue in front of `BEGIN IMMEDIATE` (`leader.WriterQueue`, 2026-10-04)
   removes the starvation; a stress test with a short timeout that fails with
   lock errors without the queue passes with it, at the same throughput.

After those changes, a sustained post-rollout observation showed healthy
leader status and zero lock errors, though that is a measurement at one load,
not a proof for future campaigns. If one writer still dominates after these
steps, partition metadata ownership or change the control-plane store; do not
force a single SQLite writer to process an unbounded global queue.

## 6. Liveness, progress, durability, and verification need separate signals

An agent heartbeat proves only that the agent can answer. A solver heartbeat
proves it is responsive, not that it is advancing. A progress counter can be
ahead of its most recent checkpoint; a local checkpoint can be ahead of its
second replica; a replicated checkpoint can be behind current work. Store and
display these separately. Do not label a long-running job failed simply
because it has not finished; use no-progress warnings and actual stall
thresholds. If a request is timed out, look for advancing bytes, cells,
checkpoints, and leases before declaring the computation unhealthy.

A graceful stop is a protocol, not an arbitrary `kill`. Latch stop intent in
the leader so a fast stop/resume cannot be missed; let the native solver reach a
committed boundary; distinguish intentional stop from engine failure; escalate
only after a bounded grace period. Launch children in supervised process groups,
drain both output pipes, bound diagnostics, and arrange for children to die if
their parent agent dies. Protect final publication with the live lease token.

Checkpoint capture needs a real consistency boundary. King Hamming's solver
stopped mutating its arrays at a checkpoint handshake while the agent copied,
hashed, fsynced, and published a versioned manifest. Copying a live mutable
array would have produced an attractive but unreliable backup. Restore checks
identity, layout, coverage, size, and hashes, then atomically installs private
mutable copies. Keep older verified checkpoints as fallback from a newer
corrupted one. See [recovery](RECOVERY.md) and [retention](RETENTION.md).

Finally, a solver's output is not the mathematical truth merely because its
process exited zero. Validate artifact structure before publication and use an
independent verifier where possible. A resource failure or incomplete search
must never be reported as a proof of no solution.

## 7. Networks and machines fail in mundane, correlated ways

The most instructive outage was not a solver bug. A Wi-Fi access-point channel
switch caused several nodes to roam, fail authentication, and remain blocked
without a desktop secrets agent. The campaign ran on survivors at about half
capacity; retained work survived, but agents on rebooted nodes did not
automatically start. The [outage investigation](NETWORK_OUTAGE_2026-10-02.md)
shows why “the node is pingable now” is not enough to explain hours of missing
compute.

For the next cluster, inventory the boring infrastructure early: wired versus
wireless topology, stable addresses, route precedence, DNS and gateway
configuration, boot-time service startup, GPU drivers, kernel compatibility,
clock synchronization, disk mounting, SSH keys, and watchdog or remote-power
options. Prefer a reliable switch for heavy data traffic. Keep control and data
addresses separate so adding the fast path does not remove the fallback.

Design for correlated failure. Three replicas all on the same switch, power
strip, or filesystem are not three independent failure domains. On a small
household cluster the chosen durability target may be pragmatic, but state the
assumption explicitly. Also distinguish “agent missing” from “storage
destroyed”: a reboot or missing heartbeat does not authorize deleting its
replica records or overwriting its work root. Revalidate on return.

## 8. Deployment is another distributed protocol

“Copy code and restart everything” was unsafe once calculations ran for
hours. We needed immutable runtime bundles with a version hash, a retained
manifest, exact process identities (PID **and** `/proc` start identity and
command), and a clear owner for every process. Never kill by a broad process
name if a precise retained identity is available. Preserve existing work,
blobs, checkpoints, and unrelated local files.

A safe rolling upgrade is a state machine:

1. Build and test the new bundle; know which code belongs to the leader,
   agents, and feeder. A source edit is **not** a live deployment.
2. Check campaign health and lease slack. A guarded leader-only restart can be
   safe without a global stop if the outage is shorter than every active
   lease's margin.
3. For an agent upgrade, drain **new assignments to one node**, but let its
   current leases complete. Wait for its active runs and partner reservations
   to reach zero. Replace exactly the owned process and runtime, wait for a
   healthy registration and revalidated storage, then resume that node.
4. Retry transient control-plane 503 responses, especially the final resume.
   A failed operator command can have partially succeeded; inspect the
   manifest, process, drain flag, and runtime version before repeating it.
5. Verify every node, version, feeder, active run, and durable artifact after
   the rollout. Observe error counters for a while, not just one successful
   HTTP response.

Global `stopped` was **not** a harmless dispatch drain here: it also latched
stop requests for active solvers. That distinction prevented an unnecessary
mass interruption. Design separate controls for “take no new work,” “request
graceful stop,” “pause one root,” and “stop the campaign.” Make the operator
interface state which one a command does.

Build compatibility into file formats and protocols so old in-flight work
can finish while new workers arrive. Do not change the interpretation of an
old tile just because a newer, more compact format exists. After an agent
restart, content bytes can survive while its advertised URL and session have
changed; revalidate the bytes and republish the address.

## 9. Reusable abstractions are boundaries, not universal frameworks

The cleanest split was:

- **Cluster runtime:** queue, leases, identity, fencing, resource reservations,
  process supervision, content-addressed transport, recovery, metrics.
- **Trusted solver adapter:** validation, resource estimate, fixed executable
  arguments, input and checkpoint interpretation, result validation, optional
  dependency workflow.
- **Campaign policy:** which calculations to attempt next, pressure between
  pipeline stages, priority, mathematical retry policy, and result table.
- **Native kernel and independent verifier:** compute and check the actual
  answer, without teaching the queue the algorithm's arrays.

The cluster must not accept arbitrary uploaded commands as “solver specs.”
Register trusted adapters and validate canonical arguments. Conversely, do
not put DP-specific tile geometry into a supposedly reusable leader. A tiny
unrelated demo adapter was valuable evidence that queue, checkpoint, and
result publication really were generic. See the [adapter guide](../cluster/ADAPTER_GUIDE.md).

Generalize after one real second use case, not after imagining ten. Keep
compatibility wrappers thin and delete or mark superseded design notes;
otherwise old instructions become an operational hazard. We found that a
static dashboard hostname map omitted a newly added worker, which also meant
its disk monitor never measured that worker. Prefer discovering inventory
from registered nodes and layering friendly names on top, with tests for new
nodes and refreshes.

## 10. Test the unhappy paths before trusting a large run

A fast unit test of the mathematical kernel is necessary but not enough. The
most valuable cluster tests in this project exercised real local leader and
agent processes, copied runtime bundles, concurrent leases, loopback blob
servers, and intentional failures. For the next project, test at least:

- Duplicate enqueue, lost response, stale lease token, expired node session,
  and two agents racing for the same reservation.
- Agent crash while a child is computing; leader restart during active leases;
  stop followed immediately by resume; a partial worker upgrade.
- Interrupted transfer, poisoned partial file, corrupt newest checkpoint,
  older checkpoint fallback, missing replica, and revalidation after a reboot.
- A writer lock held across a heartbeat or input request; transient HTTP 503;
  a failed final reconstruction with every child still durable.
- Small and large unit sizes, old and new artifact formats, a GPU absent or
  busy, memory rejection, low disk, and a narrow dependency frontier.
- A result checked independently of the implementation that produced it.

Measure production effects with repeatable before/after windows: completed
units and results, wall time, CPU/GPU busy time, bytes sent and received,
replication delay, queue wait, memory peaks, disk free, lease losses, and leader
writer wait/hold time. A canary benchmark and per-lease resource samples are
more informative than `top` alone. Keep test clusters isolated from the
retained production campaign and do not assume that passing a small case
proves a 40,000-tile database scan is cheap. Test the actual scale of the
metadata, even if the native computation is mocked.

## 11. Operator visibility and security are part of correctness

The status interface should answer *why* a machine is idle and *why* a result
is not advancing: no ready dependency, waiting for the second replica, low
disk, memory admission, paused root, reserved partner, stale heartbeat,
reconstruction, or actual failure. A “healthy” badge must not mean only that
one scheduler thread recently returned. Expose current configuration, runtime
version, and last successful measurement. Keep per-route metrics bounded so
observability itself cannot fill the database or logs.

Timestamp every log line where it is written. Until 2026-10-04 the leader, agent and
feeder logs carried no times, so a traceback couldn't be placed, and the dashboard
guessed times from when it first saw a line (forgetting them on restart). Each
long-running process now stamps its own lines (`cluster/logstamp.py`); do it inside
the process rather than through a pipe to a helper, so logging can never block on
the helper and a crash's last traceback is still written.

Provide concise ordinary status and an explicit verbose mode, plus
copy-and-paste operator commands. Record the distinction between a static
estimate, an observed peak, and a hard limit. Display stale disk data with
its measurement age and error instead of silently hiding that machine.

Treat the control plane as privileged. Authenticated UI access does not
automatically secure an unauthenticated leader HTTP port. Restrict network
exposure, use trusted adapter registration rather than arbitrary command
execution, validate paths and URLs, and maintain an audit trail for operator
actions. A tunnel should point at a locally bound authenticated dashboard;
worker storage and leader control need their own network threat model. These
were not all fully solved in this project and should be designed earlier next
time.

## 12. The weird edge cases worth remembering

These are not merely theoretical failure modes. They either happened during
this project or were exposed by its tests. Some older incident notes describe
an earlier deployed version; the point here is the invariant to carry forward,
not a claim that every current deployment has the same bug.

| Edge case we encountered | Reusable safeguard |
| --- | --- |
| A root had every child tile complete, but reconstruction timed out on a leader request. Three whole-field attempts failed even though the first attempt still had 8,281 usable tiles. | Make assembly a separately retryable phase of the **same** root. A transient transport error is not a failed calculation; check durable children before allowing a new attempt. This eventually recovered 13^9 without recomputing its grid. |
| A new 23^7 attempt did not *appear* to remember its old tiles. | Distinguish attempt lineage from content reuse. Show reused artifact counts explicitly, and avoid creating a new attempt merely to retry assembly. The newer attempt did reuse most old tile artifacts. |
| Reconstruction needed an exclusive host, but a whole-field runtime estimate put it behind smaller new tiles. Every newly freed slot was immediately refilled. | Give the closing phase its own priority and deliberately drain one candidate host. Priority alone cannot satisfy an exclusive-resource request if ordinary leases immediately consume every opening. |
| A 60-second heartbeat gap made existing replicas look gone. Replacement copies were made; when agents returned, tiles had 5–10 copies instead of the target three. | Use different liveness windows for *readable now* and *likely to return soon*. Do not erase a replica claim on a timeout, and trim confirmed surplus copies conservatively after recovery. |
| Finished fields' tile artifacts were protected forever by the generic artifact index; scratch directories also survived failed or cancelled leases. | Define retention by **reference and purpose**, not merely by row existence or run status. Keep shared tiles while any live root needs them, retire them after a durable final result, and sweep abandoned private scratch with a grace period. The campaign cleanup freed 1.23 TB. |
| A restarted agent had intact bytes but a new process/session and potentially a new blob URL. | Revalidate stored hashes and re-advertise reachable addresses under the new session. Never treat an old heartbeat or URL as proof of present availability, and never treat a lost heartbeat as proof of disk loss. |
| A resumed HTTP Range download could have a poisoned partial prefix, and a newer checkpoint could be corrupt while an older one remained good. | Hash the **entire** assembled object; discard a bad partial and retry cleanly before blaming the source. Keep older verified images and try them newest-first. Do not silently start from zero if checkpoint history exists but none is usable. |
| Copying live DP arrays could yield internally inconsistent checkpoint files even if each file later hashed correctly. | Add an application-level snapshot handshake: stop mutation, copy and fsync all members, publish one versioned manifest, then resume. Hashing alone does not create a consistent point in time. |
| The old lease holder could finish late after its lease expired or after the node rebooted. | Fence **every** progress, artifact, and completion mutation in the same transaction that checks the current lease and node session. Killing the old process is helpful but not a correctness proof. |
| The leader's scheduler health said “healthy” while worker HTTP requests timed out behind SQLite writer contention; over 100 request threads accumulated. | Report route-level latency, writer wait/hold time, lock failures, and agent-observed errors separately. A healthy background tick is not an end-to-end health check. Bound every writer transaction and socket backlog. |
| A read-looking tile-input request entered `BEGIN IMMEDIATE`; per-tile status queries and global DAG rescans multiplied the cost under load. | Separate read-only descriptor lookup from the short fenced write that actually claims work. Use bulk queries and bounded scan cadence; test with a production-sized metadata database, not only tiny fixtures. |
| A leader restart or final resume could return a transient 503 after part of the operator action had already taken effect. | Make control commands idempotent where possible. Before retrying, inspect the retained process identity, version, drain flag, and campaign state; treat “no response” as an **unknown outcome**, not definite failure. |
| The global `stopped` switch also requested active solvers to stop. It was not a harmless way to pause new leasing for a rollout. | Give dispatch drain, graceful job stop, per-root pause, and campaign shutdown different commands and state transitions. Test a stop/resume race. |
| A GPU program used the same exit code for “GPU absent” and “tile too large.” One oversized tile could disable GPU use for unrelated fitting tiles for ten minutes. | Check device fit before launch and preserve typed failure reasons. Avoid turning a resource-specific rejection into a host-wide circuit breaker. |
| Test agents could wait on the **production** host-wide GPU lock, making unrelated end-to-end tests take minutes or fail. | Isolate test resource namespaces and disable production accelerators in fixtures unless the test is specifically about them. A test cluster must not contend with live work. |
| After Wi-Fi roaming and reboots, nodes were reachable again but agents had not auto-started; one host could also be pingable while its storage or GPU driver was unavailable. | Treat boot supervision, network association, mounted storage, GPU readiness, and agent registration as separate recovery checks. Design for correlated network failure, not just individual process death. |
| A new worker absent from a hard-coded dashboard hostname map also disappeared from disk monitoring. | Discover machines from registration, then decorate them with friendly names. Test both an unknown new node and a returning node whose address changes. |
| Disk status used `-1` for “never measured” and `0` for “measured, no space.” | Model unknown, stale, and zero as distinct states. A missing sample must not be silently read as either healthy capacity or a full disk. |
| One job on the leader's machine wrote 20 GB in a burst. The leader's SQLite fsyncs on the same disk waited behind it for over a minute, nothing could renew, and when it recovered the leader expired every lease in the fleet, restarting a long matching run from scratch. | Write large outputs through in bounded pieces (fdatasync/msync every few hundred MB) and never pre-fill big files. Cap dirty pages on the leader's host. Make lease expiry stall-aware: a gap in which the leader itself committed nothing is the leader's fault, so give running leases a fresh lease before expiring any. |
| A fleet-wide worker upgrade made every finished tile look lost: a restarted agent's replica rows were deleted until it revalidated its disk, and the lost-tile rule ("no live copy past the grace") read that gap as loss. About 87% of a 40,000-tile root's finished tiles were cleared and recomputed, at every upgrade since the rule shipped, while the blobs sat on disk. Replication also re-copied everything the restarted nodes held, and retention then trimmed the surplus. | Don't delete a claim you merely haven't re-checked: keep it, marked unverified, so every reader can tell "unknown" from "gone". Decide per reader: anything that *reads bytes* (sources, dependency readiness, retirement) uses verified copies only; anything that *decides something is missing* (lost-work rules, copy targets, alerts) counts unverified copies on live nodes as present. Delete the claim only when the check fails, and alert if a check runs for long. Keep a repair that re-points cleared work at surviving artifacts. And measure *net* progress (distinct durable units gained), not completions per minute: recomputed tiles inflated throughput figures for hours. |

### Hazards exposed but not fully closed at the time of the notes

Do not copy a workaround into the next project as though it were a guarantee:

- A failed parent could leave its already-running tiles consuming the fleet;
  a cancelled child under a live parent could leave that parent waiting
  forever. Terminal-parent propagation and child replacement need explicit
  state-machine tests. See [campaign notes](../web/CAMPAIGN_NOTES.md), items 2
  and 4.
- The feeder and a manual `extend` could rewrite the same manifest without
  sharing its lock. Every writer of retained operator state needs one
  transaction or lock discipline, including CLI and UI paths (item 10).
- Timeout budgets for tile input were not scaled to halo size, and lease
  history did not always retain the phase-specific error. Record *fetch*,
  *compute*, and *publish* failures separately before deciding which retry
  budget to charge (items 5 and 6).
- A trusted home LAN and authenticated dashboard did not authenticate the
  leader's mutating HTTP endpoints. A web tunnel does not secure a second,
  independently reachable control interface (item 18).

The detailed evidence is in [campaign notes](../web/CAMPAIGN_NOTES.md),
[recovery](RECOVERY.md), [the reconstruction incident](LEADER_CONTENTION_AND_DP_RECONSTRUCTION_PLAN.md),
[DP storage](DP_STORAGE.md), and [the network outage](NETWORK_OUTAGE_2026-10-02.md).

## 13. What actually moved throughput, ranked, and how we found it

After the cluster was correct, a second phase made it fast. Every item below was
measured on the live campaign. The early wins were all in the *control plane and
the waiting*, not in the kernels; only once those were gone did the GPUs become
the limit (about 93% busy), which is the right place to end up. Numbers are from
one hardware set and field mix: use them for ordering, not as targets.

| Change | Measured effect | Lesson |
| --- | --- | --- |
| Push a copy at completion and let successors start from **one** live copy (`dependency_replicas=1`), with a recompute-if-unreachable backstop | Second copy 305 s → ~3 s after completion; running tiles 21 → 46; tiles/min 34.5 → ~60 | Gating a start on the *second* copy cost more than the risk it removed. Require the copy count the *consumer* needs (one readable source), keep the third copy as background work, and make loss cheap to recover from (recompute after a grace period, and do not charge a retry when an input was merely offline). |
| Name the next replica's node when a copy lands, instead of every node polling a shared scan | Same change as above | A poll that gives every node the same answer, and that a successful node repeats immediately, is a thundering herd. Let the producer of the event name the consumer. Keep the scan only as a backstop, and hold it after an empty result. |
| Share one cached replica-candidate scan across all nodes | Leader CPU on its two pinned cores 100% → ~8%; `select_candidate` had been ~90% of its CPU | A per-request full-table count looks harmless at one node and is catastrophic at eleven polling twice a second. Compute a bounded candidate list once for everyone (a 30 s cache; each poll walks it with indexed checks). |
| Scheduler pass works from the open frontier | A pass over every tile took ~1.2 s wall (0.2 s CPU, the rest waiting for the GIL) while holding the writer lock; now 36 ms | A tile can only be ready if a neighbour in the previous wave exists, so look only at tiles up to one wave past the newest assigned one. Do the grid-wide bookkeeping (progress, lost-tile recompute, final assembly trigger) on its own slower cadence, scaled with grid size. |
| Fix the join order of the durability query | A pass on a copy of the live database took ~4 s before the frontier change | SQLite's planner started from the 200,000-row replicas table instead of the handful of candidate tiles. Check `EXPLAIN QUERY PLAN` on a production-sized database and force the order (`CROSS JOIN`) when the planner cannot know that one side is tiny. |
| Prune resource samples at most once a minute, from an index | About a quarter of all writer-lock hold time disappeared | "Delete rows older than a week" on an unindexed table, inside the single writer lock, a few times a second, is a silent tax. Every housekeeping write needs a cadence and an index. |
| Thin routine requests (progress forwarding ≤1 per 5 s; run-control and lease renewals spaced as a fraction of the lease; empty revalidation polls take no writer lock) | With the above: writer lock held 67% → 14%, average wait 264 → 4 ms, lock errors 0 | Report immediately only what changes the state (phase, message, total, finished); everything else can be sampled. A chatty control request is a write multiplied by cores multiplied by seconds. |
| `synchronous=NORMAL` with WAL | Part of the same rollout | A power cut may lose the last few commits but cannot corrupt the database. Here those commits describe recomputable tiles, so the trade is right. Decide it explicitly by asking what a lost commit costs. |
| Compact tile format 2 (row deltas, byte planes, xz) and a shared tile planner | 0.3–1.9 → 0.002–0.02 bytes per cell; fields that were refused as "1,374,139 GiB" became feasible | Storage and transfer limits were format limits, not mathematical ones. Make the format a per-root setting, let agents read both, and keep old identities unchanged. Use **one** layout planner for the feeder and the UI, or they will disagree about what fits. |
| Edge bands instead of whole predecessor packets (earlier phase) | ~44× fewer bytes read for a 13^9 tile | See section 3. |
| Wait for the GPU in proportion to the CPU alternative: wait ≈ half the tile's estimated CPU time, clamped to 1 s–15 min | Heavy tiles stopped falling to a CPU run that was 30× slower than the GPU they had nearly reached; light tiles stopped idling two minutes for a GPU their own CPUs beat. Heavy-tile wait median now ~4–7 s | A fixed fallback timeout is wrong at both ends. Derive it from a cost model (here `cells × p³ × 6.5e-9 s` per thread, fitted to live tiles) and report the limit each tile used, so the model can be checked. |

### What did not pay, and why we kept it as opt-in

Idle-priority **CPU assist** (a tile that finds the GPU busy computes on the
node's spare CPUs) sounded free. The first version made an 8-thread niced run
of a 16 s tile take 80 s, starved by ordinary CPU-fallback runs and slowed by its
own barriers. Restricted to heavy tiles it fired for **one tile in 353**, and
that tile took 462 s against about 45 s on a GPU. The GPU-wait rule had already
removed the waits it was meant to fill. It is now off unless `KH_CPU_ASSIST=1`.
Lessons: racing two engines on one tile gains nothing when one finishes in
seconds and the other in minutes; an idle-priority job is only as fast as the
slowest thing starving it; and a feature should be kept only if a measurement
shows it helps, not because it is cheap to leave in.

### How to measure so you do not fool yourself

- **Profile CPU, not wall time.** Sampling stacks by wall clock shows threads
  *waiting*; it points at locks and sockets. The sampler that found the real
  problem (`cluster/sampler.py`, `KH_LEADER_PROFILE=1`) reads each thread's CPU
  ticks from `/proc` and charges only threads that ran. It showed one function
  using 90% of the leader. Reading the lock hold times first had misled us.
- **Look at CPU saturation before blaming a lock.** With the leader pinned to
  two cores, Python's GIL turned 0.2 s of CPU into 1.2 s of wall time *inside*
  the writer lock. The lock looked guilty; the CPU was.
- **Write the guess down, then test it.** We guessed the lease-candidate query
  was slow. It took 1 ms. The cost was somewhere else. Cheap checks first
  (`EXPLAIN QUERY PLAN`, a timer on a copy of the live database), changes second.
- **Compare windows with the same field mix.** Tiles/min rose 55 → 63 → 71 across
  three deploys, but the feeder's mix of 7^13, 29^7 and 31^7 tiles differed each
  time. Per-field, per-engine medians (`waited`, `kernel`, `lease`) from the
  runs' recorded phase timings are the honest comparison. Record those timings on
  every tile from the start (fetch, halo, GPU wait, kernel, pack, publish).
- **Say "no measurable gain" and revert the default.** Several optimizations in
  this list were *removed from the critical path* by a later one. Re-measure the
  earlier ones after each big change.
- **A cluster saturated on its accelerators is the goal, not a problem.** Once the
  fleet was ~93% GPU busy, scheduling work had hit its ceiling; the next win is
  kernel time (a 29^7 tile takes ~45 s, a 7^13 tile ~1 s) or tile mix.

### Operations lessons from the same phase

- **A database connection is not closed by its context manager.** Python's
  `with sqlite3.connect(...)` only commits or rolls back. Each request left its
  connection to garbage collection; under lock contention they piled up until the
  leader hit the 1024-descriptor soft limit ("unable to open database file") and
  stayed wedged for about seven hours with no scheduler error. Use a wrapper that
  always closes, raise `RLIMIT_NOFILE` at startup, and write the regression test
  with a warm-up request *before* taking the descriptor baseline, or it is off by
  one. Check `/v1/health` and heartbeat ages, never just that a PID exists.
- **A drain is asynchronous.** `drain` returns while tiles are still running, and
  `upgrade-workers` correctly refuses with active runs. Wait until the active
  count is zero before upgrading. After a refused upgrade, `resume` is safe and
  changes nothing; the refusal is the protocol working.
- **A small grid can slip between bookkeeping passes.** The 10 s grid-wide
  refresh stepped over the short window in which a tiny grid was only partly
  replicated. Scale the interval with the work (`min(10 s, tiles / 1000)`), and
  test the small case and the large case separately.
- **Timing-sensitive multi-process tests fail under load.** At a load average
  around 12, three or four such tests timed out on a busy dev machine yet passed
  alone. Run a failing one alone before concluding anything, but do not wave all
  failures away: one genuine regression was hiding among them.
- **Passwordless `sudo` is a tool.** `perf_event_paranoid` and `ptrace_scope`
  looked like hard blocks and were not. Try `sudo -n` before declaring a kernel
  setting a blocker.
- **Do not trust repository notes over the code and live data.** Several design
  notes described earlier deployments. Verify against the code, the leader's
  `nodes` table, and `/v1/health` before acting on them.

## 14. What I would do first on the next project

Before implementing a distributed algorithm:

1. Write one page of invariants and a state diagram: calculation, run, lease,
   child unit, artifact, replica, and terminal outcomes. Define which failures
   retry locally, elsewhere, or never.
2. Measure one machine's kernel throughput, peak RSS, scratch disk, checkpoint
   cost, and input/output bytes on representative **small and large** units.
   Put checked integer bounds and a verifier in place.
3. Build a single-node durable queue and supervised worker with a fake solver.
   Test idempotency, stale-owner fencing, stop, restart, and corrupt output.
4. Add content-addressed peer transfer and one reliable checkpoint or
   completed-unit boundary. Test a killed agent and an older-image fallback
   before adding a second real machine.
5. Add multiple hosts with explicit CPU, memory, disk, GPU and network
   topology. Demonstrate useful throughput scaling, not just node count.
6. Add a dependency scheduler only if the work truly needs one. Keep writer
   transactions bounded, reader searches outside them, and metadata scale
   tests from the outset.
7. Build a rolling deployment path and a status page that explains idle and
   blocked work **before** starting multi-day jobs. Document exact recovery
   commands and preserve runtime identities.
8. Run fault injection and a sustained canary under realistic load. Compare
   result throughput and error rates after every optimization.

For a small cluster, one leader and SQLite may be entirely sufficient if the
metadata work is brief and measured. For a larger one, the same invariants,
identities, immutable data plane, and guarded rollout remain useful even if
the queue becomes partitioned or the database changes.

### Do not cargo-cult these project-specific numbers

Our 60-second leases, five-second renewals, 2 GiB tile cap, 4096-cell tile
side, dependency durability of one live copy, three-copy artifact target, 10 GiB disk
floor, 4 GiB per-node dependency cache, 5 s progress spacing, 10 s grid-wide
refresh, and 6.5e-9 s-per-cell-step GPU-wait model were tuned
against this hardware and workload at different times. Recompute them from the
next project's measured task duration, failure domains, memory peaks, network
bandwidth, and recovery objective. The transferable lesson is the **method of
choosing and observing them**, not the constants themselves.

## Further project evidence

- [Generic cluster design](../cluster/DESIGN.md) and
  [adapter guide](../cluster/ADAPTER_GUIDE.md): runtime/algorithm boundary.
- [Continuous campaign](CONTINUOUS_CAMPAIGN.md): current-style operator
  commands, wired data routing, admission and retention rules.
- [Recovery](RECOVERY.md) and [retention](RETENTION.md): consistency,
  replication, fallback, and safe deletion.
- [DP network locality](DP_NETWORK_LOCALITY.md): measured bytes, edge bands,
  cache, and bounded soft affinity.
- [Leader contention and reconstruction](LEADER_CONTENTION_AND_DP_RECONSTRUCTION_PLAN.md):
  a full incident, measurements, fix, and guarded rollout.
- [Network outage](NETWORK_OUTAGE_2026-10-02.md): an infrastructure failure
  that the solver could not have prevented.
- [Dashboard design](../web/DESIGN.md): operational visibility, including
  disk measurement independent of agent heartbeat.
