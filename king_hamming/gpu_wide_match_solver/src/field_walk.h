#ifndef KH_FIELD_WALK_H
#define KH_FIELD_WALK_H

// Field rows for wide block matching, built in passes (docs/GPU_WIDE_MATCHING_PLAN.md).
//
// One count walk over all q labels records, for every thread chunk, how many labels each cell
// gets. That fixes where each chunk's labels go in every row, so any later walk can place the
// rows of any chosen set of cells, in parallel and ascending, without holding the other rows.
// A pass = one placement walk for its cell set; 7^13 needs about nine instead of 166 GB of rows.
//
// A label z (0 <= z < q) is stored as its low `shift` bits; the high part h = z >> shift is
// implied by per-row breakpoints. Rows ascend, so h never decreases along a row and
// bp[slot * nbp + h - 1] is the first row index whose label has high part >= h. shift is 32 in
// production (4 bytes per label); tests use smaller values to exercise many breakpoints.

#include "kh_solver.h"

#include <stdbool.h>
#include <stdint.h>

#define FW_MAX_Q (UINT64_C(1) << 40)
#define FW_MAX_BREAKPOINTS 256
#define FW_NO_SLOT UINT32_MAX

typedef struct {
    uint32_t *rows;      // nslots * f low words; the row in slot s is rows[s * f .. s * f + f)
    uint32_t *bp;        // nslots * nbp breakpoints (NULL when nbp is 0)
    uint32_t *slot;      // budget entries: cell -> slot, or FW_NO_SLOT when the cell is not held
    uint64_t nslots, budget;
    uint32_t f, nbp, shift;
} fw_rows_t;

typedef struct fw_walk fw_walk_t;

/*
 * Test that X has order q-1 modulo the monic polynomial (r+1 coefficients, low degree first).
 * Returns: true exactly for a primitive-X polynomial; works for any q below FW_MAX_Q.
 */
bool fw_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial);

/*
 * First primitive-X polynomial at or after packed candidate `start` (as kh_generate_polynomial).
 * Returns: true and fills polynomial/chosen, or false when the candidates are exhausted.
 */
bool fw_generate(const kh_parameters_t *parameters, uint64_t start, uint16_t *polynomial, uint64_t *chosen);

/* Progress callback: `done` of `total` labels in the current walk. */
typedef void (*fw_progress_t)(uint64_t done, uint64_t total, void *context);

/*
 * The count walk. Checks the SUD partition (F labels per cell) and the generator's cycle.
 * Returns: an owned walk, or NULL with an error text.
 */
fw_walk_t *fw_walk_prepare(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                           uint32_t shift, fw_progress_t progress, void *context, const char **error);

/*
 * One placement walk: the rows of `cells` (ascending, distinct, each below the budget) in slots
 * 0 .. ncells-1 in that order. Checks every row's label count and a sample of labels against
 * independently computed powers. Returns: true with owned rows, or false with an error text.
 */
bool fw_walk_rows(fw_walk_t *walk, const uint64_t *cells, uint64_t ncells, fw_progress_t progress, void *context,
                  fw_rows_t *output, const char **error);

/* Bytes fw_walk_prepare keeps for the whole run (per-chunk offsets of every cell). */
uint64_t fw_walk_bytes(const kh_parameters_t *parameters, uint32_t threads, uint32_t shift);

/* Bytes one pass's rows take: rows and breakpoints of ncells, the cell-to-slot map, and positions. */
uint64_t fw_rows_bytes(const kh_parameters_t *parameters, uint32_t threads, uint32_t shift, uint64_t ncells);

/* Bytes per held row (low words and breakpoints), for sizing passes. */
uint64_t fw_row_bytes(const kh_parameters_t *parameters, uint32_t shift);

void fw_walk_free(fw_walk_t *walk);

/* Rows of cells 0 .. limit-1 in one pass (tests and small fields). */
bool fw_build_rows(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                   uint64_t limit, uint32_t shift, fw_rows_t *output, const char **error);

/* Allocate an empty rows structure for ncells slots (rows, breakpoints, an all-empty slot map). */
bool fw_rows_alloc(fw_rows_t *rows, uint64_t budget, uint32_t f, uint32_t nbp, uint32_t shift, uint64_t ncells);

/* High part of row index k in a row with nbp ascending breakpoints. */
static inline uint64_t fw_high(const uint32_t *bp, uint32_t nbp, uint32_t k) {
    uint32_t lo = 0, hi = nbp;   // count of breakpoints <= k
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (bp[mid] <= k) lo = mid + 1; else hi = mid;
    }
    return lo;
}

/* Full label at row index k of the row in a slot. */
static inline uint64_t fw_label_slot(const fw_rows_t *rows, uint64_t slot, uint32_t k) {
    uint64_t high = rows->nbp ? fw_high(rows->bp + slot * rows->nbp, rows->nbp, k) : 0;
    return (uint64_t)rows->rows[slot * rows->f + k] + (high << rows->shift);
}

/* Full label at row index k of a held cell (the cell must have a slot). */
static inline uint64_t fw_label(const fw_rows_t *rows, uint64_t cell, uint32_t k) {
    return fw_label_slot(rows, rows->slot[cell], k);
}

/* Release rows, breakpoints and the slot map. */
void fw_free(fw_rows_t *rows);

#endif
