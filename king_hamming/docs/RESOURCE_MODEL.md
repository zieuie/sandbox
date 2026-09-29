# Work ordering and resource model

This document turns the initial cluster measurements and scheduling preferences
into working design limits. The constants are starting points to validate with
benchmarks, not permanent file-format limits.

## An open-ended result table

The campaign has no fixed largest entry. The coordinator lazily generates a
bounded lookahead window of candidate calculations, estimates each candidate's
remaining runtime, and dispatches the shortest job that fits an available
machine. As jobs finish, it extends the window. The operator stops the campaign
at the publication deadline.

Concurrent machines and imperfect estimates mean completion cannot be strictly
ordered. The scheduler nevertheless prioritizes increasing estimated runtime
and records predicted versus actual time so the model improves. A small aging
term prevents a repeatedly overestimated entry from waiting forever. A manual
submission may override normal priority without changing the mathematical job
identity.

DP and matching have separate estimates and queue states. A completed DP result
may therefore appear before its matching attempt, and every matching attempt
reuses the durable DP artifact.

## Integer bounds

Field elements and powers of `q` use `uint32_t`, with checked arithmetic before
conversion. Counts, products, DP values, offsets, and work estimates use
`uint64_t`; quantities near `q^2` almost exhaust 64 bits when `q` approaches
`UINT32_MAX`.

The bound `q = p^r <= UINT32_MAX`, together with odd `r >= 3`, implies `r <= 31`,
so `r` fits in `uint8_t`. It does **not** imply that `p` fits in `uint8_t`:
when `r = 3`, primes as large as roughly the cube root of `UINT32_MAX` (about
1625) are representable. Store `p` in `uint16_t` unless the campaign explicitly
adopts `p <= 251` as a mathematical search limit.

These bounds describe representability, not practical feasibility. In
particular, enumerating all `p^3` raw DP transitions is already unreasonable at
the upper end. The production algorithm needs proved dominance reductions,
sparse generation, or another formulation before those cases become viable.

## DP dimensions and cell representation

For `r = 2m + 1`, let

- `F = p^m`;
- `B = pF = p^(m+1)`;
- the dense DP table have `(B + 1)^2` cells.

The initial distributed representation stores two unpadded arrays:

- an 8-byte optimum value per cell;
- a 4-byte transition identifier per cell.

This is 12 bytes per persistent cell. Keeping the arrays separate avoids the
16-byte stride that ordinary structure padding may introduce. A transition ID
must be checked against the number of reduced transitions before this format is
accepted; use 64-bit IDs or a different reconstruction encoding if 32 bits are
insufficient.

A complete dense table is often too large, so workers operate on tiles and
retain only the predecessor data required by the recurrence. If a transition
can subtract up to `p^2` in either coordinate, a tile depends on every earlier
tile intersecting that southwest dependency band, rather than only its immediate
left and lower neighbors.

## Standard DP tile

Use a logical tile of **4096 by 4096 cells** on every node initially. Edge tiles
may be smaller. One full tile contains 16,777,216 cells and its persistent
payload is:

| Array | Bytes | Binary size |
| --- | ---: | ---: |
| 64-bit values | 134,217,728 | 128 MiB |
| 32-bit choices | 67,108,864 | 64 MiB |
| Total | 201,326,592 | 192 MiB |

For comparison, a raw 12-byte square occupying an entire 2 GiB allowance has a
side of 13,377 cells. A padded 16-byte cell reduces that side to 11,585. Using
either maximum as the operational tile would leave no space for predecessor
bands, reduced transitions, queues, scratch memory, networking, or checkpoint
serialization. A 4096 tile is deliberately conservative and cacheable in
smaller strips while keeping network and scheduling overhead low.

Tile size is part of task planning, not artifact identity. All machines use the
standard size when it fits; the coordinator may split an edge or exceptional
tile without changing the final DP result. Benchmarks may later justify a new
cluster-wide default.

## Per-machine memory admission

The eight smaller nodes have about 15.5 GiB for eight logical CPUs. After a
minimum 2 GiB reserve for the OS, node agent, filesystem cache, and uploads,
running eight memory-heavy workers permits only about 1.68 GiB per worker. Thus
2 GiB per core is a hardware ratio and ceiling, not a safe allocation target.

The node agent admits a task only after adding:

1. resident immutable field data;
2. live DP or matching arrays;
3. predecessor tiles or matching frontier;
4. per-thread scratch and queues;
5. one bounded streaming checkpoint buffer;
6. the node reserve.

The standard tile's 192 MiB persistent payload leaves substantial space within
that budget. Implementations must report a checked byte estimate before
allocation and enforce both a task limit and a machine-wide limit. They should
stream checkpoints instead of holding a second full in-memory image.

On merlin (`uther` in the current inventory), reserve one complete physical
core, including both SMT siblings, for the coordinator, node agent, storage
service, and ordinary OS work. Its remaining seven physical cores provide at
most 14 pinned logical compute workers. Merlin may compute ordinary standard
tiles and should receive tasks needing its larger RAM after leader resources
are reserved.

## Progress and the thirty-minute threshold

Thirty minutes applies to a lack of measurable progress, not to total job
duration. Large calculations may legitimately run for hours while counters
advance.

Use these initial timings:

| Signal or action | Initial interval |
| --- | ---: |
| Solver liveness heartbeat | 10 seconds |
| Node summary to coordinator | 30 seconds |
| Human-readable active-job status | 1 minute and phase changes |
| No progress-counter movement: warning and diagnostics | 5 minutes |
| No progress after diagnostics: recovery incident | 30 minutes |

Every heartbeat reports a monotone phase-specific counter, such as cells
committed, rows scanned, BFS vertices visited, augmentations completed, or bytes
uploaded. A changing counter proves progress even when no artifact has completed.
At five minutes without movement, collect state and expose a warning. At thirty
minutes, checkpoint if possible, restart the solver once through the node agent,
and then follow the agreed agent-restart and one-reboot incident policy if the
agent itself is unavailable.

Checkpoint boundaries remain algorithmic: a completed DP tile or dependency
wave, and a completed valid matching phase. The leader controls a configurable
checkpoint interval, initially 30 minutes per calculation regardless of how many
machines participate. It schedules checkpoint work early enough for measured
write, replication, and upload time. If safe algorithmic boundaries cannot meet
the interval, status reports the excess and a finer resumable boundary must be
designed.

## Measurements needed before freezing constants

The first local production prototypes should report peak resident bytes,
transition count after reductions, cells or vertices per second, checkpoint
size and time, and time lost when restoring. Those measurements will calibrate
runtime ordering, prove whether 4096 is a useful standard tile, and identify the
first cases that require more than one machine for a single calculation.
