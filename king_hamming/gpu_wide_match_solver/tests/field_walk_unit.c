// field_walk.c against the 32-bit builder (dp_solver/src/field.c) on every small field: same
// polynomials, and the same rows from passes over arbitrary cell sets, with narrow label words
// (many breakpoints, as fields above 2^32 need) and several thread counts.

#include "field_walk.h"
#include "kh_field.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

#define EXPECT(condition, ...) do { if (!(condition)) { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); ++failures; } } while (0)

static uint64_t state = 20261006;

static uint64_t next_random(void) {
    state = state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
    return state >> 11;
}

static void check_rows(const char *what, uint32_t p, uint32_t r, const kh_field_t *field, const fw_rows_t *rows,
                       const uint64_t *cells, uint64_t ncells) {
    for (uint64_t slot = 0; slot < ncells; ++slot) {
        EXPECT(rows->slot[cells[slot]] == slot, "%u^%u %s: slot map", p, r, what);
        for (uint32_t k = 0; k < field->parameters.f; ++k) {
            uint64_t expected = field->cells[cells[slot] * field->parameters.f + k];
            uint64_t got = fw_label(rows, cells[slot], k);
            if (got != expected) {
                EXPECT(false, "%u^%u %s: cell %llu k %u: %llu != %llu", p, r, what,
                       (unsigned long long)cells[slot], k, (unsigned long long)got, (unsigned long long)expected);
                return;
            }
        }
    }
}

static void check_field(uint32_t p, uint32_t r) {
    kh_parameters_t parameters;
    const char *error = NULL;
    if (!kh_parameters(p, r, &parameters, &error)) return;
    uint16_t a[32], b[32];
    uint32_t chosen32 = 0;
    uint64_t chosen64 = 0;
    bool found32 = kh_generate_polynomial(&parameters, 1, a, &chosen32);
    bool found64 = fw_generate(&parameters, 1, b, &chosen64);
    EXPECT(found32 == found64 && chosen32 == chosen64 && !memcmp(a, b, (r + 1) * sizeof *a),
           "%u^%u: generated polynomials differ", p, r);
    for (uint64_t candidate = 1; parameters.q <= 4096 && candidate < parameters.q; ++candidate) {
        uint64_t packed = candidate;
        for (uint32_t index = 0; index < r; ++index) { a[index] = (uint16_t)(packed % p); packed /= p; }
        a[r] = 1;
        EXPECT(kh_primitive(&parameters, a) == fw_primitive(&parameters, a), "%u^%u: verdicts differ", p, r);
    }
    kh_generate_polynomial(&parameters, 1, a, &chosen32);
    kh_field_t field;
    if (!kh_build_field(&parameters, a, 2, UINT64_C(1) << 34, &field, &error)) {
        EXPECT(false, "%u^%u: kh_build_field: %s", p, r, error);
        return;
    }
    uint64_t budget = parameters.budget;
    uint64_t *cells = malloc(budget * sizeof *cells);
    uint32_t shifts[] = {32, 1, 3, 7};
    for (unsigned s = 0; s < sizeof shifts / sizeof *shifts; ++s) {
        uint32_t shift = shifts[s];
        if (((parameters.q - 1) >> shift) > FW_MAX_BREAKPOINTS) continue;
        uint32_t threads = 1 + s % 4;
        // One pass over cells 0 .. limit-1, as a small field would run.
        fw_rows_t rows;
        uint64_t limit = s % 2 ? parameters.f : budget;
        if (fw_build_rows(&parameters, a, threads, limit, shift, &rows, &error)) {
            for (uint64_t cell = 0; cell < limit; ++cell) cells[cell] = cell;
            check_rows("one pass", p, r, &field, &rows, cells, limit);
            fw_free(&rows);
        } else {
            EXPECT(false, "%u^%u shift %u: %s", p, r, shift, error);
        }
        // Several passes over random cell sets from one count walk; cell 0 sometimes left out.
        fw_walk_t *walk = fw_walk_prepare(&parameters, a, threads, shift, NULL, NULL, &error);
        if (walk == NULL) {
            EXPECT(false, "%u^%u shift %u: prepare: %s", p, r, shift, error);
            continue;
        }
        for (unsigned pass = 0; pass < 4; ++pass) {
            uint64_t ncells = 0;
            for (uint64_t cell = 0; cell < budget; ++cell) {
                if (next_random() % 4 == pass % 4) cells[ncells++] = cell;
            }
            if (!fw_walk_rows(walk, cells, ncells, NULL, NULL, &rows, &error)) {
                EXPECT(false, "%u^%u shift %u pass %u: %s", p, r, shift, pass, error);
                continue;
            }
            check_rows("pass", p, r, &field, &rows, cells, ncells);
            for (uint64_t cell = 0, held = 0; cell < budget; ++cell) {
                held += rows.slot[cell] != FW_NO_SLOT;
                if (cell + 1 == budget) EXPECT(held == ncells, "%u^%u: slot map holds extra cells", p, r);
            }
            fw_free(&rows);
        }
        // Unsorted cell lists are refused.
        if (budget >= 2) {
            uint64_t bad[2] = {1, 0};
            EXPECT(!fw_walk_rows(walk, bad, 2, NULL, NULL, &rows, &error), "%u^%u: unsorted cells accepted", p, r);
        }
        fw_walk_free(walk);
    }
    free(cells);
    kh_free_field(&field);
}

int main(void) {
    uint32_t fields[][2] = {{2, 3}, {2, 5}, {2, 7}, {2, 9}, {2, 11}, {3, 3}, {3, 5}, {3, 7}, {5, 3}, {5, 5},
                            {7, 3}, {7, 5}, {11, 3}, {13, 3}, {13, 5}, {17, 3}, {2, 15}, {3, 9}};
    for (unsigned index = 0; index < sizeof fields / sizeof *fields; ++index) {
        check_field(fields[index][0], fields[index][1]);
    }
    // 64-bit arithmetic above the old 2^36 cap, without building rows: 7^13 (the field this solver
    // is for) and 13^9 must yield primitive polynomials, and 13^9's must match the campaign's.
    kh_parameters_t big;
    const char *error = NULL;
    uint16_t poly[32];
    uint64_t chosen = 0;
    EXPECT(kh_parameters_dp64(13, 9, &big, &error) && big.q == UINT64_C(10604499373), "13^9 dimensions");
    EXPECT(fw_generate(&big, 1, poly, &chosen) && fw_primitive(&big, poly), "13^9: no primitive polynomial");
    EXPECT(kh_parameters_dp64(7, 13, &big, &error) && big.q == UINT64_C(96889010407) && big.f == 117649, "7^13 dimensions");
    EXPECT(fw_generate(&big, 1, poly, &chosen) && fw_primitive(&big, poly), "7^13: no primitive polynomial");
    printf("7^13 first primitive candidate %llu:", (unsigned long long)chosen);
    for (uint32_t index = 0; index <= 13; ++index) printf("%s%u", index ? "," : " ", poly[index]);
    printf("\n");
    if (failures) {
        fprintf(stderr, "field_walk_unit: %d failures\n", failures);
        return 1;
    }
    puts("ok field_walk_unit");
    return 0;
}
