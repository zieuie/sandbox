#ifndef KH_PROGRESS_H
#define KH_PROGRESS_H

#include "kh_threads.h"

// Owned periodic reporter independent of mathematical worker threads.
typedef struct kh_progress_t kh_progress_t;

/*
 * Start periodic JSON heartbeat and progress output on stdout.
 * Parameters:
 *   pool: Input live pool, retained until reporter shutdown.
 *   baseline: Cells already covered by the restart checkpoint.
 *   checkpoint_tiles: Initial committed tile cursor.
 *   total: Number of positive-budget DP cells.
 *   threads: Configured solver worker count, used in diagnostics.
 *   milliseconds: Positive heartbeat interval.
 *   error: Output static diagnostic on failure.
 * Returns: Owned reporter or NULL; emits the initial status before returning.
 */
kh_progress_t *kh_progress_start(
    kh_pool_t *pool,
    uint64_t baseline,
    uint64_t checkpoint_tiles,
    uint64_t total,
    uint32_t threads,
    uint64_t milliseconds,
    const char **error
);

/*
 * Change the reported phase independently of mathematical progress.
 * Parameters: progress: Live reporter; phase: Static JSON-safe string with program lifetime.
 * Returns: No value; emits current state with the new phase.
 */
void kh_progress_phase(kh_progress_t *progress, const char *phase);

/*
 * Record a durable local checkpoint after its arrays and metadata are committed.
 * Parameters: progress: Live reporter; tiles: Committed tile cursor.
 * Returns: No value; records the current completed-cell count as durable and emits it.
 */
void kh_progress_checkpoint(kh_progress_t *progress, uint64_t tiles);

/*
 * Join the reporter before releasing the pool it observes.
 * Parameters: progress: Owned reporter, or NULL.
 * Returns: No value; releases reporter synchronization and memory.
 */
void kh_progress_stop(kh_progress_t *progress);

#endif
