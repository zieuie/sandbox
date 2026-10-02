#define _GNU_SOURCE

#include "kh_field.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Build one production field with the shared dp_solver builder and dump its cells.
 * Usage: kh_field_export P R C0,...,Cr|auto OUT.bin [THREADS] [MAX_BYTES]
 * Output: "KHGF", u32 p, u32 r, u64 q, u32 f, u32 reserved, then q little-endian u32 cell labels.
 * Returns: Zero on success; one on any error.
 */
int main(int argc, char **argv) {
    if (argc < 5) {
        puts("Dump the production SUD cell table for the GPU matcher.\n"
             "Usage: kh_field_export P R C0,...,Cr|auto OUT.bin [THREADS] [MAX_BYTES]\n"
             "Example: ./kh_field_export 5 3 2,3,0,1 /tmp/5_3.khgf 4");
        return argc == 1 ? 0 : 1;
    }
    uint32_t p = (uint32_t)strtoul(argv[1], NULL, 10);
    uint32_t r = (uint32_t)strtoul(argv[2], NULL, 10);
    uint32_t threads = argc > 5 ? (uint32_t)strtoul(argv[5], NULL, 10) : 1;
    uint64_t maximum = argc > 6 ? strtoull(argv[6], NULL, 10) : UINT64_C(17179869184);
    const char *error = NULL;
    kh_parameters_t parameters;

    if (!kh_parameters(p, r, &parameters, &error)) {
        fprintf(stderr, "kh_field_export: %s\n", error);
        return 1;
    }
    uint16_t polynomial[32] = {0};
    uint32_t candidate = 0;

    // "auto" mirrors the production kernel's first automatic candidate.
    if (!strcmp(argv[3], "auto")) {
        if (!kh_generate_polynomial(&parameters, 1, polynomial, &candidate)) {
            fprintf(stderr, "kh_field_export: primitive polynomial candidates exhausted\n");
            return 1;
        }
        goto build;
    }
    char *copy = strdup(argv[3]);
    char *cursor = copy;

    // Exactly r+1 comma-separated coefficients, low degree first.
    for (uint32_t index = 0; index <= r; ++index) {
        char *comma = strchr(cursor, ',');
        if ((comma == NULL) != (index == r)) {
            fprintf(stderr, "kh_field_export: polynomial needs %u coefficients\n", r + 1);
            return 1;
        }
        if (comma != NULL) {
            *comma = '\0';
        }
        polynomial[index] = (uint16_t)strtoul(cursor, NULL, 10);
        cursor = comma == NULL ? cursor : comma + 1;
    }
    free(copy);

    if (!kh_primitive(&parameters, polynomial)) {
        fprintf(stderr, "kh_field_export: polynomial is not primitive with generator X\n");
        return 1;
    }
build:;
    kh_field_t field = {0};

    if (!kh_build_field(&parameters, polynomial, threads, maximum, &field, &error)) {
        fprintf(stderr, "kh_field_export: %s\n", error);
        return 1;
    }
    FILE *file = fopen(argv[4], "wb");
    uint32_t header[6] = {p, r, (uint32_t)parameters.q, (uint32_t)(parameters.q >> 32), parameters.f, 0};
    bool success = file != NULL && fwrite("KHGF", 1, 4, file) == 4 &&
                   fwrite(header, sizeof header, 1, file) == 1 &&
                   fwrite(field.cells, sizeof(uint32_t), parameters.q, file) == parameters.q;

    if (file != NULL && fclose(file) != 0) {
        success = false;
    }
    kh_free_field(&field);
    if (!success) {
        fprintf(stderr, "kh_field_export: write failed\n");
        return 1;
    }
    printf("{\"candidate\":%u,\"polynomial\":[", candidate);
    for (uint32_t index = 0; index <= r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    puts("]}");
    return 0;
}
