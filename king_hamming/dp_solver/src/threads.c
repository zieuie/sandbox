#define _GNU_SOURCE

#include "kh_threads.h"

#include <pthread.h>
#include <sched.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>

// Logical CPU with its physical-core identity and sibling rank.
typedef struct {
    int cpu;
    int package;
    int core;
    unsigned rank;
} cpu_entry_t;

// Per-worker immutable ownership and CPU placement.
typedef struct {
    struct kh_pool_t *pool;
    pthread_t thread;
    uint32_t index;
    int cpu;
} worker_t;

// Persistent synchronization and one caller-owned rectangle at a time.
struct kh_pool_t {
    uint32_t count;
    uint32_t created;
    uint32_t ready;
    uint32_t completed;
    uint64_t generation;
    _Atomic uint64_t progress_cells;
    bool stopping;
    bool affinity_failed;
    bool mutex_initialized;
    bool condition_initialized;
    bool barrier_initialized;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    pthread_barrier_t diagonal_barrier;
    worker_t *workers;
    kh_cell_function_t function;
    void *context;
    uint32_t first_u;
    uint32_t last_u;
    uint32_t first_v;
    uint32_t last_v;
};

/*
 * Read one sysfs topology integer.
 * Parameters: cpu: Logical CPU; name: Topology basename; fallback: Value on read failure.
 * Returns: Parsed topology value, or fallback when the topology is unavailable.
 */
static int topology_value(int cpu, const char *name, int fallback) {
    char path[256];
    snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/topology/%s", cpu, name);
    FILE *file = fopen(path, "r");

    // Missing topology is handled conservatively as a distinct physical core.
    if (file == NULL) {
        return fallback;
    }
    int value;
    int scanned = fscanf(file, "%d", &value);
    fclose(file);
    return scanned == 1 ? value : fallback;
}

/*
 * Order one logical CPU per physical core before additional siblings.
 * Parameters: left: First cpu_entry_t; right: Second cpu_entry_t.
 * Returns: Negative, zero, or positive according to placement order.
 */
static int compare_cpus(const void *left, const void *right) {
    const cpu_entry_t *a = left;
    const cpu_entry_t *b = right;

    // Sibling rank is more important than logical CPU numbering.
    if (a->rank != b->rank) {
        return a->rank < b->rank ? -1 : 1;
    }
    return (a->cpu > b->cpu) - (a->cpu < b->cpu);
}

/*
 * Choose worker CPUs within the inherited process affinity mask.
 * Parameters: workers: Output records; count: Requested worker count; error: Failure diagnostic.
 * Returns: True when every worker receives a distinct allowed logical CPU.
 */
static bool assign_cpus(worker_t *workers, uint32_t count, const char **error) {
    cpu_set_t allowed;

    // Respect the agent's CPU allocation instead of using all online processors.
    if (sched_getaffinity(0, sizeof allowed, &allowed) != 0) {
        *error = "cannot read Linux CPU affinity";
        return false;
    }
    cpu_entry_t entries[CPU_SETSIZE];
    uint32_t available = 0;

    // Gather the physical topology for every permitted logical CPU.
    for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu) {
        if (!CPU_ISSET(cpu, &allowed)) {
            continue;
        }
        cpu_entry_t entry = {0};
        entry.cpu = cpu;
        entry.package = topology_value(cpu, "physical_package_id", 0);
        entry.core = topology_value(cpu, "core_id", cpu);

        // Earlier allowed siblings receive lower placement ranks.
        for (uint32_t previous = 0; previous < available; ++previous) {
            if (entries[previous].package == entry.package &&
                entries[previous].core == entry.core) {
                ++entry.rank;
            }
        }
        entries[available++] = entry;
    }

    // Oversubscription requires an explicit future policy, not silent reuse.
    if (count > available) {
        *error = "--threads exceeds the allowed logical CPU count";
        return false;
    }
    qsort(entries, available, sizeof entries[0], compare_cpus);

    // Give each worker one distinct CPU, physical cores first.
    for (uint32_t index = 0; index < count; ++index) {
        workers[index].cpu = entries[index].cpu;
    }
    return true;
}

/*
 * Expose the same physical-core-first placement to other native solvers.
 * Parameters: count: Requested workers; cpus: Output CPU numbers; error: Static diagnostic on failure.
 * Returns: True after selecting distinct allowed CPUs; false after releasing scratch.
 */
bool kh_select_cpus(uint32_t count, int *cpus, const char **error) {

    // Reject invalid counts before allocating temporary worker records.
    if (count == 0 || count > CPU_SETSIZE || cpus == NULL) {
        *error = "invalid worker CPU count";
        return false;
    }
    worker_t *workers = calloc(count, sizeof *workers);

    // A failed allocation has no usable CPU selection to return.
    if (workers == NULL) {
        *error = "cannot allocate CPU placement scratch";
        return false;
    }
    bool valid = assign_cpus(workers, count, error);

    // Copy stable CPU numbers before discarding private placement records.
    if (valid) {
        for (uint32_t index = 0; index < count; ++index) {
            cpus[index] = workers[index].cpu;
        }
    }
    free(workers);
    return valid;
}

/*
 * Evaluate the current rectangle's diagonals for one worker.
 * Parameters: worker: Worker with fixed pool and disjoint range ownership.
 * Returns: No value; all workers leave each diagonal only after its barrier.
 */
static void fill_rectangle(worker_t *worker) {
    kh_pool_t *pool = worker->pool;
    uint64_t first = (uint64_t)pool->first_u + pool->first_v;
    uint64_t last = (uint64_t)pool->last_u + pool->last_v;

    // Every predecessor has a strictly lower sum than its dependent cell.
    for (uint64_t diagonal = first; diagonal <= last; ++diagonal) {
        uint64_t lower = pool->first_u;
        uint64_t upper = pool->last_u;

        // Intersect the diagonal with the rectangle's right boundary.
        if (diagonal > pool->last_v && diagonal - pool->last_v > lower) {
            lower = diagonal - pool->last_v;
        }

        // Intersect the diagonal with the rectangle's left boundary.
        if (diagonal - pool->first_v < upper) {
            upper = diagonal - pool->first_v;
        }
        uint64_t cells = upper - lower + 1;
        // Spread expensive central cells and cheaper boundary cells across
        // every worker. Ownership stays disjoint and ties remain cell-local.
        for (uint64_t u = lower + worker->index; u <= upper; u += pool->count) {
            pool->function(pool->context, (uint32_t)u, (uint32_t)(diagonal - u));
        }

        // Publish predecessor writes before the next diagonal reads them.
        if (pool->count > 1) {
            pthread_barrier_wait(&pool->diagonal_barrier);
        }

        // Count a diagonal only after every worker has published its writes.
        if (worker->index == 0) {
            atomic_fetch_add_explicit(&pool->progress_cells, cells, memory_order_relaxed);
        }
    }
}

/*
 * Pin one persistent worker and wait for successive rectangle generations.
 * Parameters: argument: Input worker_t with lifetime spanning the thread.
 * Returns: NULL when the owner requests shutdown.
 */
static void *worker_main(void *argument) {
    worker_t *worker = argument;
    kh_pool_t *pool = worker->pool;
    cpu_set_t affinity;
    CPU_ZERO(&affinity);
    CPU_SET(worker->cpu, &affinity);
    int affinity_status = pthread_setaffinity_np(pthread_self(), sizeof affinity, &affinity);
    pthread_mutex_lock(&pool->mutex);

    // Report startup failure before any mathematical work is submitted.
    if (affinity_status != 0) {
        pool->affinity_failed = true;
    }
    ++pool->ready;
    pthread_cond_broadcast(&pool->condition);
    uint64_t generation = 0;

    // Sleep between tiles instead of recreating threads for every diagonal.
    while (!pool->stopping) {
        while (!pool->stopping && pool->generation == generation) {
            pthread_cond_wait(&pool->condition, &pool->mutex);
        }

        // Shutdown is always requested while the pool is idle.
        if (pool->stopping) {
            break;
        }
        generation = pool->generation;
        pthread_mutex_unlock(&pool->mutex);
        fill_rectangle(worker);
        pthread_mutex_lock(&pool->mutex);
        ++pool->completed;
        pthread_cond_broadcast(&pool->condition);
    }
    pthread_mutex_unlock(&pool->mutex);
    return NULL;
}

/*
 * Join all workers and release an idle pool.
 * Parameters: pool: Owned idle pool, or NULL.
 * Returns: No value; invalidates pool and releases its allocations.
 */
void kh_pool_destroy(kh_pool_t *pool) {

    // Permit cleanup of failed allocations and partial initialization.
    if (pool == NULL) {
        return;
    }

    // Wake created workers without entering a partially populated barrier.
    if (pool->created != 0) {
        pthread_mutex_lock(&pool->mutex);
        pool->stopping = true;
        pthread_cond_broadcast(&pool->condition);
        pthread_mutex_unlock(&pool->mutex);

        // Join each successfully created worker before releasing shared data.
        for (uint32_t index = 0; index < pool->created; ++index) {
            pthread_join(pool->workers[index].thread, NULL);
        }
    }

    // Destroy only synchronization objects whose initialization succeeded.
    if (pool->barrier_initialized) {
        pthread_barrier_destroy(&pool->diagonal_barrier);
    }
    if (pool->condition_initialized) {
        pthread_cond_destroy(&pool->condition);
    }
    if (pool->mutex_initialized) {
        pthread_mutex_destroy(&pool->mutex);
    }
    free(pool->workers);
    free(pool);
}

/*
 * Allocate workers and pin them within the caller's allowed Linux CPU set.
 * Parameters: threads: Worker count; function: Evaluator; context: Shared state; error: Diagnostic.
 * Returns: Owned idle pool, or NULL after cleaning up an initialization failure.
 */
kh_pool_t *kh_pool_create(
    uint32_t threads,
    kh_cell_function_t function,
    void *context,
    const char **error
) {

    // Validate count before allocating worker records.
    if (threads == 0 || threads > CPU_SETSIZE || function == NULL) {
        *error = "invalid worker count or cell evaluator";
        return NULL;
    }
    kh_pool_t *pool = calloc(1, sizeof *pool);

    // Allocation failures do not leave an owned pool behind.
    if (pool == NULL) {
        *error = "cannot allocate thread pool";
        return NULL;
    }
    pool->count = threads;
    atomic_init(&pool->progress_cells, 0);
    pool->function = function;
    pool->context = context;
    pool->workers = calloc(threads, sizeof *pool->workers);

    // Assign placement before launching workers.
    if (pool->workers == NULL || !assign_cpus(pool->workers, threads, error)) {
        if (pool->workers == NULL) {
            *error = "cannot allocate worker records";
        }
        kh_pool_destroy(pool);
        return NULL;
    }

    // The single-thread path needs neither thread creation nor barriers.
    if (threads == 1) {
        pool->workers[0].pool = pool;
        cpu_set_t affinity;
        CPU_ZERO(&affinity);
        CPU_SET(pool->workers[0].cpu, &affinity);

        // Apply the same explicit placement contract to the calling thread.
        if (pthread_setaffinity_np(pthread_self(), sizeof affinity, &affinity) != 0) {
            *error = "cannot pin the single DP worker";
            kh_pool_destroy(pool);
            return NULL;
        }
        return pool;
    }
    *error = "cannot initialize thread-pool synchronization";

    // Initialize resources in dependency order for safe partial cleanup.
    if (pthread_mutex_init(&pool->mutex, NULL) != 0) {
        kh_pool_destroy(pool);
        return NULL;
    }
    pool->mutex_initialized = true;
    if (pthread_cond_init(&pool->condition, NULL) != 0) {
        kh_pool_destroy(pool);
        return NULL;
    }
    pool->condition_initialized = true;
    if (pthread_barrier_init(&pool->diagonal_barrier, NULL, threads) != 0) {
        kh_pool_destroy(pool);
        return NULL;
    }
    pool->barrier_initialized = true;

    // Create the fixed pool once, before any tile is admitted.
    for (uint32_t index = 0; index < threads; ++index) {
        worker_t *worker = &pool->workers[index];
        worker->pool = pool;
        worker->index = index;

        // Failed thread creation wakes and joins only the already created workers.
        if (pthread_create(&worker->thread, NULL, worker_main, worker) != 0) {
            *error = "cannot create DP worker";
            kh_pool_destroy(pool);
            return NULL;
        }
        ++pool->created;
    }
    pthread_mutex_lock(&pool->mutex);

    // Wait for placement acknowledgments before returning a usable pool.
    while (pool->ready < threads) {
        pthread_cond_wait(&pool->condition, &pool->mutex);
    }
    bool failed = pool->affinity_failed;
    pthread_mutex_unlock(&pool->mutex);

    // A CPU placement failure must not silently oversubscribe another calculation.
    if (failed) {
        *error = "cannot pin a DP worker";
        kh_pool_destroy(pool);
        return NULL;
    }
    return pool;
}

/*
 * Evaluate a rectangle in synchronized ascending u+v antidiagonals.
 * Parameters: pool: Idle pool; first_u,last_u: Stripe bounds; first_v,last_v: Symbol bounds.
 * Returns: No value; waits until all worker writes are complete.
 */
void kh_pool_fill(
    kh_pool_t *pool,
    uint32_t first_u,
    uint32_t last_u,
    uint32_t first_v,
    uint32_t last_v
) {

    // Use the same traversal without synchronization overhead for one worker.
    if (pool->count == 1) {
        pool->first_u = first_u;
        pool->last_u = last_u;
        pool->first_v = first_v;
        pool->last_v = last_v;
        fill_rectangle(&pool->workers[0]);
        return;
    }
    pthread_mutex_lock(&pool->mutex);
    pool->first_u = first_u;
    pool->last_u = last_u;
    pool->first_v = first_v;
    pool->last_v = last_v;
    pool->completed = 0;
    ++pool->generation;
    pthread_cond_broadcast(&pool->condition);

    // The completion handshake also publishes writes for checkpoint flushing.
    while (pool->completed < pool->count) {
        pthread_cond_wait(&pool->condition, &pool->mutex);
    }
    pthread_mutex_unlock(&pool->mutex);
}

/*
 * Inspect the fixed placement of one initialized worker.
 * Parameters: pool: Initialized pool; index: Worker index below its configured count.
 * Returns: Assigned Linux logical CPU number.
 */
int kh_pool_cpu(const kh_pool_t *pool, uint32_t index) {
    return pool->workers[index].cpu;
}

/*
 * Read completed cell work without touching mutable DP state.
 * Parameters: pool: Initialized pool that remains alive during the call.
 * Returns: Cells completed in this invocation, including uncommitted tiles.
 */
uint64_t kh_pool_progress(const kh_pool_t *pool) {
    return atomic_load_explicit(&pool->progress_cells, memory_order_relaxed);
}
