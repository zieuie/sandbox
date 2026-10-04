// field_prefix.c against the 32-bit builder it generalizes (dp_solver/src/field.c), on every
// small field: same polynomials, same rows; and with narrow label words, the same labels
// rebuilt from breakpoints.

#include "field_prefix.h"
#include "kh_field.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

#define EXPECT(condition, ...) do { if (!(condition)) { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); ++failures; } } while (0)

static void check_field(uint32_t p, uint32_t r) {
    kh_parameters_t parameters;
    const char *error = NULL;
    if (!kh_parameters(p, r, &parameters, &error)) return;
    uint16_t a[32], b[32];
    uint32_t chosen32 = 0;
    uint64_t chosen64 = 0;
    bool found32 = kh_generate_polynomial(&parameters, 1, a, &chosen32);
    bool found64 = fp_generate(&parameters, 1, b, &chosen64);
    EXPECT(found32 == found64 && chosen32 == chosen64 && !memcmp(a, b, (r + 1) * sizeof *a),
           "%u^%u: generated polynomials differ", p, r);
    // Every candidate gets the same primitivity verdict (small fields only).
    for (uint64_t candidate = 1; parameters.q <= 4096 && candidate < parameters.q; ++candidate) {
        uint64_t packed = candidate;
        for (uint32_t index = 0; index < r; ++index) { a[index] = (uint16_t)(packed % p); packed /= p; }
        a[r] = 1;
        EXPECT(kh_primitive(&parameters, a) == fp_primitive(&parameters, a), "%u^%u: verdicts differ", p, r);
    }
    kh_generate_polynomial(&parameters, 1, a, &chosen32);
    kh_field_t field;
    if (!kh_build_field(&parameters, a, 2, UINT64_C(1) << 34, &field, &error)) {
        EXPECT(false, "%u^%u: kh_build_field: %s", p, r, error);
        return;
    }
    uint32_t shifts[] = {32, 1, 3, 7};
    for (unsigned s = 0; s < sizeof shifts / sizeof *shifts; ++s) {
        uint32_t shift = shifts[s];
        if (((parameters.q - 1) >> shift) > FP_MAX_BREAKPOINTS) continue;
        uint64_t limits[] = {parameters.budget, parameters.f, 1};
        for (unsigned l = 0; l < 3; ++l) {
            fp_rows_t rows;
            uint32_t threads = 1 + (s + l) % 4;
            if (!fp_build_rows(&parameters, a, threads, limits[l], shift, NULL, NULL, &rows, &error)) {
                EXPECT(false, "%u^%u shift %u: %s", p, r, shift, error);
                continue;
            }
            for (uint64_t cell = 0; cell < limits[l]; ++cell) {
                for (uint32_t k = 0; k < parameters.f; ++k) {
                    uint64_t expected = field.cells[cell * parameters.f + k];
                    if (fp_label(&rows, cell, k) != expected) {
                        EXPECT(false, "%u^%u shift %u limit %llu: cell %llu k %u: %llu != %llu", p, r, shift,
                               (unsigned long long)limits[l], (unsigned long long)cell, k,
                               (unsigned long long)fp_label(&rows, cell, k), (unsigned long long)expected);
                        cell = limits[l];
                        break;
                    }
                }
            }
            fp_free(&rows);
        }
    }
    kh_free_field(&field);
}

int main(void) {
    uint32_t fields[][2] = {{2, 3}, {2, 5}, {2, 7}, {2, 9}, {2, 11}, {3, 3}, {3, 5}, {3, 7}, {5, 3}, {5, 5},
                            {7, 3}, {7, 5}, {11, 3}, {13, 3}, {13, 5}, {17, 3}, {2, 15}, {3, 9}};
    for (unsigned index = 0; index < sizeof fields / sizeof *fields; ++index) {
        check_field(fields[index][0], fields[index][1]);
    }
    // 64-bit arithmetic on a field above 2^32 (13^9): the polynomial the campaign pins, and its
    // generator order, without building rows.
    kh_parameters_t big;
    const char *error = NULL;
    EXPECT(kh_parameters_dp64(13, 9, &big, &error) && big.q == UINT64_C(10604499373), "13^9 dimensions");
    uint16_t poly[32];
    uint64_t chosen = 0;
    EXPECT(fp_generate(&big, 1, poly, &chosen), "13^9: no primitive polynomial found");
    EXPECT(fp_primitive(&big, poly), "13^9: generated polynomial is not primitive");
    printf("13^9 first primitive candidate %llu:", (unsigned long long)chosen);
    for (uint32_t index = 0; index <= 9; ++index) printf("%s%u", index ? "," : " ", poly[index]);
    printf("\n");
    if (failures) {
        fprintf(stderr, "field_prefix_unit: %d failures\n", failures);
        return 1;
    }
    puts("ok field_prefix_unit");
    return 0;
}
