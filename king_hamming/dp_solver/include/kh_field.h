#ifndef KH_FIELD_H
#define KH_FIELD_H

#include "kh_solver.h"

// One shared immutable SUD table; labels always enumerate powers of X starting with label 1=1.
typedef struct {
    kh_parameters_t parameters;
    uint16_t polynomial[32];
    uint32_t *cells;
    uint64_t allocated_bytes;
} kh_field_t;

/*
 * Test that X has order q-1 in the polynomial quotient.
 * Parameters: parameters: Valid field dimensions; polynomial: r+1 coefficients, low degree first.
 * Returns: True exactly for a monic primitive polynomial with fixed generator X.
 */
bool kh_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial);

/*
 * Generate the first primitive polynomial at or after a packed lower-coefficient candidate.
 * Parameters: parameters: Valid dimensions; start: Candidate lower coefficients; polynomial: r+1 output coefficients; chosen: Output packed candidate.
 * Returns: True when a primitive-X polynomial is found; false when the candidate range is exhausted.
 */
bool kh_generate_polynomial(const kh_parameters_t *parameters, uint32_t start, uint16_t *polynomial, uint32_t *chosen);

/*
 * Build one shared field partition without storing complete logarithm or coefficient tables.
 * Parameters: parameters: Valid dimensions; polynomial: Validated r+1 coefficients; threads: Pinned worker count; max_bytes: Memory payload limit; output: Receives owned cells; error: Failure diagnostic.
 * Returns: True on success; false with no owned field storage on failure.
 */
bool kh_build_field(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads, uint64_t max_bytes, kh_field_t *output, const char **error);

/*
 * Release an owned field partition.
 * Parameters: field: Previously initialized field or zero-initialized structure.
 * Returns: No value; releases and clears its owned cell pointer.
 */
void kh_free_field(kh_field_t *field);

#endif
