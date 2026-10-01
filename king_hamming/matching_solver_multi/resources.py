"""Conservative native-owner, transport, and coordinator memory envelopes."""
from matching_solver_multi.state import owned_count

MIB = 1024**2


def verifier_memory(dp):
    return 4 * dp["q"] + 4 * dp["budget"] + 2 * ((dp["q"] + 7) // 8) + 64 * MIB


def partitioned_memory(dp, workers, threads, batch):
    q, f = dp["q"], dp["f"]
    # Bound every incoming frame, allocator growth and compute/I/O stacks.
    records = workers * (batch * workers + 1) + 64 * workers
    scratch = 16 * batch + 48 * records + 8 * MIB * (threads + workers) + 128 * MIB
    peaks = []
    for rank in range(workers):
        cells = max(0, (dp["budget"] - 1 - rank) // workers + 1)
        right = ((q * (rank + 1) + workers - 1) // workers - (q * rank + workers - 1) // workers)
        state = 28 * owned_count(dp, workers, rank) + 16 * right + 4 * cells * (f + 1) + 8 * ((q + 63) // 64)
        peaks.append(state + scratch + 16 * len(dp["runs"]))
    worker = max(peaks)
    # Image validation uses a second q-bit uniqueness map while the native
    # owner is paused. Final field verification runs after all owners exit.
    return max(worker + (q + 7) // 8 + 64 * MIB, verifier_memory(dp) + 64 * MIB), worker


def admitted_memory(dp, workers, threads, batch, margin):
    return tuple((size * (100 + margin) + 99) // 100 for size in partitioned_memory(dp, workers, threads, batch))
