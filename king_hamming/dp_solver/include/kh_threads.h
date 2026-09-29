#ifndef KH_THREADS_H
#define KH_THREADS_H

#include <stdbool.h>
#include <stdint.h>

// Opaque persistent pool; callers own it until kh_pool_destroy.
typedef struct kh_pool_t kh_pool_t;

// Cell evaluator; context is shared and writes must be confined to (u,v).
typedef void (*kh_cell_function_t)(void *context, uint32_t u, uint32_t v);

/*
 * Select distinct allowed logical CPUs, placing physical cores before siblings.
 * Parameters: count: Requested worker count; cpus: Output array with count slots; error: Static diagnostic on failure.
 * Returns: True after filling cpus; false for unavailable CPUs or allocation failure.
 */
bool kh_select_cpus(uint32_t count, int *cpus, const char **error);

/*
 * Allocate workers and pin them within the caller's allowed Linux CPU set.
 * Parameters:
 *   threads: Positive worker count, at most the number of allowed logical CPUs.
 *   function: Cell evaluator called once per cell in each submitted rectangle.
 *   context: Shared evaluator state, valid until the pool is destroyed.
 *   error: Output static diagnostic string on failure.
 * Returns: Owned pool, or NULL after cleaning up a failed initialization.
 */
kh_pool_t *kh_pool_create(
    uint32_t threads,
    kh_cell_function_t function,
    void *context,
    const char **error
);

/*
 * Evaluate a rectangle in synchronized ascending u+v antidiagonals.
 * Parameters:
 *   pool: Idle initialized pool; only one caller may submit work.
 *   first_u: Positive minimum stripe coordinate.
 *   last_u: Maximum stripe coordinate, at least first_u.
 *   first_v: Positive minimum symbol coordinate.
 *   last_v: Maximum symbol coordinate, at least first_v.
 * Returns: No value; waits until all cells and worker writes are complete.
 */
void kh_pool_fill(
    kh_pool_t *pool,
    uint32_t first_u,
    uint32_t last_u,
    uint32_t first_v,
    uint32_t last_v
);

/*
 * Join all workers and release an idle pool.
 * Parameters: pool: Owned idle pool, or NULL.
 * Returns: No value; invalidates pool and releases its allocations.
 */
void kh_pool_destroy(kh_pool_t *pool);

/*
 * Inspect the fixed placement of one initialized worker.
 * Parameters: pool: Initialized pool; index: Worker index below its configured count.
 * Returns: Assigned Linux logical CPU number.
 */
int kh_pool_cpu(const kh_pool_t *pool, uint32_t index);

/*
 * Read completed cell work without touching mutable DP state.
 * Parameters: pool: Initialized pool that remains alive during the call.
 * Returns: Cells completed in this invocation, including uncommitted tiles.
 */
uint64_t kh_pool_progress(const kh_pool_t *pool);

#endif
