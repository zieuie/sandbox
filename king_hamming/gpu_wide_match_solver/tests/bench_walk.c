// Time the count walk and one placement walk on a real field (CPU and RAM only), to size passes:
// tests/bench_walk P R THREADS [EIGHTHS]  holds EIGHTHS/8 of the used cells (default 1) in the pass.

#include "field_walk.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static double seconds(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s P R THREADS [EIGHTHS]\n", argv[0]);
        return 1;
    }
    kh_parameters_t parameters;
    const char *error = NULL;
    uint32_t p = (uint32_t)atoi(argv[1]), r = (uint32_t)atoi(argv[2]), threads = (uint32_t)atoi(argv[3]);
    uint64_t eighths = argc > 4 ? (uint64_t)atoi(argv[4]) : 1;
    if (!kh_parameters_dp64(p, r, &parameters, &error)) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    uint16_t polynomial[32];
    uint64_t chosen;
    if (!fw_generate(&parameters, 1, polynomial, &chosen)) return 1;
    double t0 = seconds();
    fw_walk_t *walk = fw_walk_prepare(&parameters, polynomial, threads, 32, NULL, NULL, &error);
    if (walk == NULL) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    double t1 = seconds();
    uint64_t ncells = parameters.budget * eighths / 8;
    uint64_t *cells = malloc(ncells * sizeof *cells);
    for (uint64_t index = 0; index < ncells; ++index) cells[index] = index;
    fw_rows_t rows;
    if (!fw_walk_rows(walk, cells, ncells, NULL, NULL, &rows, &error)) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    double t2 = seconds();
    printf("{\"p\":%u,\"r\":%u,\"q\":%" PRIu64 ",\"threads\":%u,\"count_seconds\":%.2f,\"place_seconds\":%.2f,"
           "\"cells\":%" PRIu64 ",\"count_labels_per_second\":%.3e,\"place_labels_per_second\":%.3e}\n",
           p, r, parameters.q, threads, t1 - t0, t2 - t1, ncells, (double)parameters.q / (t1 - t0),
           (double)parameters.q / (t2 - t1));
    fw_free(&rows);
    fw_walk_free(walk);
    free(cells);
    return 0;
}
