// Time fp_build_rows on a real field: ./tests/bench_field P R A THREADS [POLY]
// Builds the rows of cells 0 .. A*F-1 (A = the DP's largest a), checks that every row ascends,
// and prints seconds and bytes as JSON. CPU and RAM only; no GPU.

#include "field_prefix.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    if (argc < 5) {
        puts("Usage: bench_field P R A THREADS [C0,...,Cr]\nExample: ./tests/bench_field 13 9 5 8");
        return argc == 1 ? 0 : 1;
    }
    kh_parameters_t parameters;
    const char *error = NULL;
    if (!kh_parameters_dp64((uint32_t)atoi(argv[1]), (uint32_t)atoi(argv[2]), &parameters, &error)) {
        fprintf(stderr, "bench_field: %s\n", error);
        return 1;
    }
    uint64_t a = strtoull(argv[3], NULL, 10), threads = strtoull(argv[4], NULL, 10);
    uint16_t polynomial[32] = {0};
    uint64_t chosen = 0;
    if (argc > 5) {
        char *cursor = argv[5];
        for (uint32_t index = 0; index <= parameters.r; ++index) {
            polynomial[index] = (uint16_t)strtoul(cursor, &cursor, 10);
            if (*cursor == ',') ++cursor;
        }
    } else if (!fp_generate(&parameters, 1, polynomial, &chosen)) {
        fprintf(stderr, "bench_field: no primitive polynomial\n");
        return 1;
    }
    double start = now();
    fp_rows_t rows;
    if (!fp_build_rows(&parameters, polynomial, (uint32_t)threads, a * parameters.f, 32, NULL, NULL, &rows, &error)) {
        fprintf(stderr, "bench_field: %s\n", error);
        return 1;
    }
    double built = now();
    for (uint64_t cell = 0; cell < rows.limit; ++cell) {
        for (uint32_t k = 1; k < rows.f; ++k) {
            if (fp_label(&rows, cell, k - 1) >= fp_label(&rows, cell, k)) {
                fprintf(stderr, "bench_field: row %" PRIu64 " does not ascend at %u\n", cell, k);
                return 1;
            }
        }
    }
    printf("{\"q\":%" PRIu64 ",\"f\":%u,\"cells\":%" PRIu64 ",\"breakpoints\":%u,\"bytes\":%" PRIu64
           ",\"build_seconds\":%.1f,\"check_seconds\":%.1f}\n", parameters.q, parameters.f, rows.limit, rows.nbp,
           fp_build_bytes(&parameters, (uint32_t)threads, rows.limit, 32), built - start, now() - built);
    fp_free(&rows);
    return 0;
}
