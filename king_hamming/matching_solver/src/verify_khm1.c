#define _GNU_SOURCE

// Streaming verifier for KHM1 full matchings. It is deliberately independent of the solvers: it
// shares no code with dp_solver/src/field.c or any matching kernel, and mirrors the arithmetic of
// the Python verifier (artifacts.py: field_cells and verify) instead. Python checks the
// header, checksum and payload length; this program checks every edge and endpoint uniqueness.
//
// Usage: kh_verify_khm1 P R C0,...,Cr BLOCKS.txt FILE OFFSET
// BLOCKS.txt: "<runs>\n" then one "<a> <copies>" line per run, as for kh_match_kernel.
// FILE at OFFSET holds n packed choices of bit_length(F-1) bits, low bit first, then zero padding.
// Prints {"assigned":N} and exits 0, or prints a reason to stderr and exits 1.
// Memory: 4*F*amax*F bytes of cell rows, q/8 bytes of endpoints, and 4*budget bytes of counters.

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fail(const char *message) {
    fprintf(stderr, "kh_verify_khm1: %s\n", message);
    return 1;
}

static bool parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    *value = strtoull(text, &end, 10);
    return end != text && *end == '\0';
}

int main(int argc, char **argv) {
    if (argc == 1) {
        puts("Verify the edges of a KHM1 full matching without storing the field.\n"
             "Usage: kh_verify_khm1 P R C0,...,Cr BLOCKS.txt FILE OFFSET\n"
             "Example: kh_verify_khm1 3 3 1,2,0,1 blocks.txt result.khmatch 52");
        return 0;
    }
    uint64_t p, r, offset;
    if (argc != 7 || !parse_u64(argv[1], &p) || !parse_u64(argv[2], &r) || !parse_u64(argv[6], &offset) ||
        p < 2 || p > 65535 || r < 2 || r > 31) {
        return fail("invalid arguments");
    }
    uint64_t q = 1;
    for (uint64_t i = 0; i < r; ++i) {
        q *= p;
        if (q > UINT32_MAX) return fail("field too large");
    }
    uint64_t f = 1;
    for (uint64_t i = 0; i < r / 2; ++i) f *= p;
    uint64_t budget = p * f, qm1 = q - 1;

    uint64_t poly[32];
    const char *cursor = argv[3];
    for (uint64_t i = 0; i <= r; ++i) {
        char *end = NULL;
        poly[i] = strtoull(cursor, &end, 10);
        if (end == cursor || poly[i] >= p || (i < r ? *end != ',' : *end != '\0')) return fail("invalid polynomial");
        cursor = end + 1;
    }
    if (poly[r] != 1) return fail("polynomial is not monic");

    FILE *blocks = fopen(argv[4], "r");
    uint64_t runs = 0, amax = 0, n = 0, cosets = 0;
    if (blocks == NULL || fscanf(blocks, "%" SCNu64, &runs) != 1 || runs == 0 || runs > budget) return fail("invalid blocks file");
    uint64_t *run_a = calloc(runs, 8), *run_copies = calloc(runs, 8);
    if (run_a == NULL || run_copies == NULL) return fail("allocation failed");
    for (uint64_t i = 0; i < runs; ++i) {
        if (fscanf(blocks, "%" SCNu64 " %" SCNu64, &run_a[i], &run_copies[i]) != 2 || run_a[i] == 0 ||
            run_a[i] > p || run_copies[i] == 0) return fail("invalid blocks file");
        if (run_a[i] > amax) amax = run_a[i];
        n += run_a[i] * run_copies[i] * f;
        cosets += run_copies[i];
        if (n >= qm1 * f || cosets >= qm1) return fail("blocks exceed the field");
    }
    fclose(blocks);
    uint64_t limit = amax * f;  // requests use only cells 0 .. amax*F-1
    if (limit > budget || limit * f > UINT32_MAX) return fail("cell rows too large");

    // Field cells, as artifacts.field_cells: label 0 first, then labels 1..q-1 follow X^0, X^1, ...
    uint32_t *rows = malloc(limit * f * 4);
    uint32_t *counts = calloc(budget, 4);
    if (rows == NULL || counts == NULL) return fail("allocation failed");
    uint64_t half = r / 2, top_place = q / p, current = 1, packed = 0;
    for (uint64_t j = 0, place = 1; j < r; ++j, place *= p) packed += poly[j] * place;
    for (uint64_t label = 0; label < q; ++label) {
        uint64_t element = label == 0 ? 0 : current;
        uint64_t suffix = element % f, high = element / f, prefix = 0;
        if (p == 2) {
            prefix = (uint64_t)__builtin_parityll(high);
        } else {
            for (uint64_t i = 0; i < r - half; ++i) {
                prefix += high % p;
                high /= p;
            }
        }
        uint64_t cell = (prefix % p) * f + suffix;
        if (counts[cell] >= f) return fail("invalid SUD bucket size");
        if (cell < limit) rows[cell * f + counts[cell]] = (uint32_t)label;
        ++counts[cell];
        if (label != 0) {
            uint64_t top = current / top_place, rest = (current % top_place) * p;
            if (p == 2) {
                current = rest ^ (top ? packed : 0);
            } else {
                current = 0;
                for (uint64_t j = 0, place = 1; j < r; ++j, place *= p) {
                    uint64_t digit = (rest % p + p - top * poly[j] % p) % p;
                    current += digit * place;
                    rest /= p;
                }
            }
        }
    }
    if (current != 1) return fail("invalid primitive cycle");
    for (uint64_t cell = 0; cell < budget; ++cell) {
        if (counts[cell] != f) return fail("invalid SUD partition");
    }
    free(counts);

    // Stream the packed choices in canonical request order and mark each right endpoint once.
    uint32_t bits = 0;
    for (uint64_t value = f - 1; value != 0; value >>= 1) ++bits;
    uint8_t *used = calloc((q + 7) / 8, 1);
    FILE *input = fopen(argv[5], "rb");
    if (used == NULL || input == NULL || fseek(input, (long)offset, SEEK_SET) != 0) return fail("cannot read the certificate");
    static uint8_t buffer[1 << 20];
    size_t have = 0, at = 0;
    uint64_t accumulator = 0, available = 0, assigned = 0, coset = 0;
    for (uint64_t run = 0; run < runs; ++run) {
        for (uint64_t copy = 0; copy < run_copies[run]; ++copy, ++coset) {
            for (uint64_t prefix = 0; prefix < run_a[run]; ++prefix) {
                for (uint64_t suffix = 0; suffix < f; ++suffix) {
                    while (available < bits) {
                        if (at == have) {
                            have = fread(buffer, 1, sizeof buffer, input);
                            at = 0;
                            if (have == 0) return fail("truncated choices");
                        }
                        accumulator |= (uint64_t)buffer[at++] << available;
                        available += 8;
                    }
                    uint64_t choice = bits ? accumulator & ((UINT64_C(1) << bits) - 1) : 0;
                    accumulator >>= bits;
                    available -= bits;
                    if (choice >= f) return fail("neighbor index out of range");
                    uint64_t label = rows[(prefix * f + suffix) * f + choice];
                    uint64_t right = label == 0 ? 0 : 1 + (label - 1 + qm1 - coset % qm1) % qm1;
                    if (used[right >> 3] >> (right & 7) & 1) return fail("matching repeats a right endpoint");
                    used[right >> 3] |= (uint8_t)(1u << (right & 7));
                    ++assigned;
                }
            }
        }
    }
    if (accumulator != 0) return fail("nonzero padding");
    printf("{\"assigned\":%" PRIu64 ",\"requests\":%" PRIu64 "}\n", assigned, n);
    return 0;
}
