#ifndef KH_FIELD_PREFIX_H
#define KH_FIELD_PREFIX_H

// 64-bit field construction for block matching: the cell rows of only the first `limit` cells
// (the cells that requests use), for fields with q up to 2^36.
//
// A label z (0 <= z < q) is stored as its low `shift` bits; the high part h = z >> shift is
// implied by per-row breakpoints. Rows ascend, so h never decreases along a row and
// bp[cell * nbp + h - 1] is the first row index whose label has high part >= h. With
// q <= 2^shift there are no breakpoints and the low word is the label itself. shift is 32 in
// production (4 bytes per label, however large q is); tests use smaller values to exercise
// the breakpoints on small fields.

#include "kh_solver.h"

#include <stdbool.h>
#include <stdint.h>

#define FP_MAX_Q (UINT64_C(1) << 36)
#define FP_MAX_BREAKPOINTS 64

typedef struct {
    uint32_t *rows;      // limit * f low words; row of cell c is rows[c * f .. c * f + f)
    uint32_t *bp;        // limit * nbp breakpoints (NULL when nbp is 0)
    uint64_t limit;      // cells stored: 0 .. limit - 1
    uint32_t f, nbp, shift;
} fp_rows_t;

/*
 * Test that X has order q-1 modulo the monic polynomial (r+1 coefficients, low degree first).
 * Returns: true exactly for a primitive-X polynomial; works for any q below FP_MAX_Q.
 */
bool fp_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial);

/*
 * First primitive-X polynomial at or after packed candidate `start` (as kh_generate_polynomial).
 * Returns: true and fills polynomial/chosen, or false when the candidates are exhausted.
 */
bool fp_generate(const kh_parameters_t *parameters, uint64_t start, uint16_t *polynomial, uint64_t *chosen);

/* Progress callback: `done` of `total` work units (each label is visited twice: count, then place). */
typedef void (*fp_progress_t)(uint64_t done, uint64_t total, void *context);

/*
 * Build the rows of cells 0 .. limit-1 with `threads` threads. Every label is still enumerated
 * (to place each one in the right row), but only used rows are stored: 4*limit*F bytes plus
 * breakpoints, instead of 4q. Checks the SUD partition (F labels per cell), the generator's
 * cycle, and a sample of labels against independently computed powers.
 * Returns: true with owned rows, or false with an error text and nothing allocated.
 */
bool fp_build_rows(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                   uint64_t limit, uint32_t shift, fp_progress_t progress, void *context,
                   fp_rows_t *output, const char **error);

/* Bytes fp_build_rows allocates for these arguments (rows, breakpoints, and counters). */
uint64_t fp_build_bytes(const kh_parameters_t *parameters, uint32_t threads, uint64_t limit, uint32_t shift);

/* Full label at row index k of a stored cell. */
static inline uint64_t fp_label(const fp_rows_t *rows, uint64_t cell, uint32_t k) {
    uint64_t high = 0;
    const uint32_t *bp = rows->bp + cell * rows->nbp;
    for (uint32_t index = 0; index < rows->nbp; ++index) {
        high += k >= bp[index];
    }
    return (uint64_t)rows->rows[cell * rows->f + k] + (high << rows->shift);
}

/* Release rows and breakpoints. */
void fp_free(fp_rows_t *rows);

#endif
