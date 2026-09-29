#define _POSIX_C_SOURCE 200809L

#include "kh_progress.h"

#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

// Reporter fields serialized by one mutex; cell counts are read atomically from the pool.
struct kh_progress_t {
    kh_pool_t *pool;
    uint64_t baseline;
    uint64_t checkpoint_done;
    uint64_t checkpoint_tiles;
    uint64_t total;
    uint64_t milliseconds;
    uint32_t threads;
    const char *phase;
    bool stopping;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    pthread_t reporter;
};

/*
 * Emit one consistent status snapshot while holding the reporter mutex.
 * Parameters: progress: Locked reporter whose pool remains alive.
 * Returns: No value; writes and flushes one complete JSON line.
 */
static void emit_locked(kh_progress_t *progress) {
    uint64_t done = progress->baseline + kh_pool_progress(progress->pool);
    printf("{\"done\":%" PRIu64 ",\"total\":%" PRIu64
           ",\"checkpoint_done\":%" PRIu64 ",\"checkpoint_tiles\":%" PRIu64
           ",\"threads\":%u,\"units\":\"cells\",\"phase\":\"%s\","
           "\"message\":\"dp cells\",\"heartbeat\":true}\n",
           done,
           progress->total,
           progress->checkpoint_done,
           progress->checkpoint_tiles,
           progress->threads,
           progress->phase);
    fflush(stdout);
}

/*
 * Emit heartbeats even when computation or checkpoint I/O is quiet.
 * Parameters: argument: Input live kh_progress_t owned by the calling process.
 * Returns: NULL after shutdown is requested.
 */
static void *reporter_main(void *argument) {
    kh_progress_t *progress = argument;
    pthread_mutex_lock(&progress->mutex);

    // Monotonic timed waits are unaffected by adjustments to the wall clock.
    while (!progress->stopping) {
        struct timespec deadline;
        clock_gettime(CLOCK_MONOTONIC, &deadline);
        deadline.tv_sec += (time_t)(progress->milliseconds / 1000);
        deadline.tv_nsec += (long)(progress->milliseconds % 1000) * 1000000L;

        // Normalize the subsecond part before passing it to pthreads.
        if (deadline.tv_nsec >= 1000000000L) {
            ++deadline.tv_sec;
            deadline.tv_nsec -= 1000000000L;
        }
        int status = 0;

        // Spurious wakeups do not cause a tight heartbeat loop.
        while (!progress->stopping && status != ETIMEDOUT) {
            status = pthread_cond_timedwait(&progress->condition, &progress->mutex, &deadline);
        }

        // Publish a snapshot only while the owner still wants reports.
        if (!progress->stopping) {
            emit_locked(progress);
        }
    }
    pthread_mutex_unlock(&progress->mutex);
    return NULL;
}

/*
 * Start periodic JSON heartbeat and progress output on stdout.
 * Parameters:
 *   pool: Live worker pool; baseline: Durable cells; checkpoint_tiles: Durable cursor.
 *   total: DP cell count; threads: Worker count; milliseconds: Positive interval.
 *   error: Output diagnostic on failure.
 * Returns: Owned reporter or NULL; emits an initial status on success.
 */
kh_progress_t *kh_progress_start(
    kh_pool_t *pool,
    uint64_t baseline,
    uint64_t checkpoint_tiles,
    uint64_t total,
    uint32_t threads,
    uint64_t milliseconds,
    const char **error
) {
    kh_progress_t *progress = calloc(1, sizeof *progress);

    // Reject invalid timing before creating synchronization resources.
    if (progress == NULL || milliseconds == 0 || milliseconds > 86400000) {
        free(progress);
        *error = "cannot allocate progress reporter or invalid heartbeat interval";
        return NULL;
    }
    progress->pool = pool;
    progress->baseline = baseline;
    progress->checkpoint_done = baseline;
    progress->checkpoint_tiles = checkpoint_tiles;
    progress->total = total;
    progress->threads = threads;
    progress->milliseconds = milliseconds;
    progress->phase = "computing";

    // Initialize synchronization in dependency order.
    if (pthread_mutex_init(&progress->mutex, NULL) != 0) {
        free(progress);
        *error = "cannot initialize progress mutex";
        return NULL;
    }
    pthread_condattr_t attributes;
    int status = pthread_condattr_init(&attributes);

    // Configure the same clock used to construct heartbeat deadlines.
    if (status == 0) {
        status = pthread_condattr_setclock(&attributes, CLOCK_MONOTONIC);
        if (status == 0) {
            status = pthread_cond_init(&progress->condition, &attributes);
        }
        pthread_condattr_destroy(&attributes);
    }

    // A failed condition initialization leaves no running reporter.
    if (status != 0) {
        pthread_mutex_destroy(&progress->mutex);
        free(progress);
        *error = "cannot initialize progress condition";
        return NULL;
    }
    emit_locked(progress);

    // Start one small reporter independent of tile-worker completion.
    if (pthread_create(&progress->reporter, NULL, reporter_main, progress) != 0) {
        pthread_cond_destroy(&progress->condition);
        pthread_mutex_destroy(&progress->mutex);
        free(progress);
        *error = "cannot create progress reporter";
        return NULL;
    }
    return progress;
}

/*
 * Change the reported phase independently of mathematical progress.
 * Parameters: progress: Live reporter; phase: Static JSON-safe string.
 * Returns: No value; emits current state with the new phase.
 */
void kh_progress_phase(kh_progress_t *progress, const char *phase) {
    pthread_mutex_lock(&progress->mutex);
    progress->phase = phase;
    emit_locked(progress);
    pthread_mutex_unlock(&progress->mutex);
}

/*
 * Record a durable local checkpoint after its arrays and metadata are committed.
 * Parameters: progress: Live reporter; tiles: Committed tile cursor.
 * Returns: No value; records current work as durable and emits it.
 */
void kh_progress_checkpoint(kh_progress_t *progress, uint64_t tiles) {
    pthread_mutex_lock(&progress->mutex);
    progress->checkpoint_done = progress->baseline + kh_pool_progress(progress->pool);
    progress->checkpoint_tiles = tiles;
    emit_locked(progress);
    pthread_mutex_unlock(&progress->mutex);
}

/*
 * Join the reporter before releasing the pool it observes.
 * Parameters: progress: Owned reporter, or NULL.
 * Returns: No value; releases reporter synchronization and memory.
 */
void kh_progress_stop(kh_progress_t *progress) {

    // Allow cleanup paths to accept an uninitialized reporter.
    if (progress == NULL) {
        return;
    }
    pthread_mutex_lock(&progress->mutex);
    progress->stopping = true;
    pthread_cond_signal(&progress->condition);
    pthread_mutex_unlock(&progress->mutex);
    pthread_join(progress->reporter, NULL);
    pthread_cond_destroy(&progress->condition);
    pthread_mutex_destroy(&progress->mutex);
    free(progress);
}
