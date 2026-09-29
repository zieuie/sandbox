#define _POSIX_C_SOURCE 200809L

#include "kh_progress.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

/*
 * Simulate a slow cell without advancing its completed-diagonal counter.
 * Parameters: context: Unused; u: Unused stripe coordinate; v: Unused symbol coordinate.
 * Returns: No value; sleeps for approximately 250 milliseconds.
 */
static void slow_cell(void *context, uint32_t u, uint32_t v) {
    (void)context;
    (void)u;
    (void)v;
    struct timespec delay = {.tv_sec = 0, .tv_nsec = 250000000L};
    nanosleep(&delay, NULL);
}

/*
 * Exercise independent heartbeats while a cell is still unfinished.
 * Parameters: argc: Argument count; argv: Command arguments; --run selects the test.
 * Returns: Zero for help or a completed test, one on startup failure.
 */
int main(int argc, char **argv) {

    // Keep the standard no-argument help convention for this test executable.
    if (argc == 1) {
        puts("Exercise heartbeat and work-progress separation.\nExample: ./build/progress_unit --run");
        return 0;
    }

    // Require an explicit test invocation.
    if (argc != 2 || strcmp(argv[1], "--run")) {
        return 1;
    }
    const char *error;
    kh_pool_t *pool = kh_pool_create(1, slow_cell, NULL, &error);

    // Avoid launching a reporter without a usable pool.
    if (pool == NULL) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    kh_progress_t *progress = kh_progress_start(pool, 0, 0, 1, 1, 50, &error);

    // Clean up a failed reporter initialization before leaving the test.
    if (progress == NULL) {
        kh_pool_destroy(pool);
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    kh_pool_fill(pool, 1, 1, 1, 1);
    kh_progress_checkpoint(progress, 1);
    kh_progress_stop(progress);
    kh_pool_destroy(pool);
    return 0;
}
