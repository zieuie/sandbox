#ifndef KH_SOLVER_H
#define KH_SOLVER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Mathematical parameters derived from an odd prime power.
typedef struct {
    uint16_t p;
    uint8_t r;
    uint32_t q;
    uint32_t f;
    uint32_t budget;
} kh_parameters_t;

// One transition in the paper's ascending a, b, t order.
typedef struct {
    uint16_t a;
    uint16_t b;
    uint16_t t;
    uint16_t reserved;
    uint32_t gain;
} kh_transition_t;

// Checked resource counts for the dense tiled DP representation.
typedef struct {
    uint64_t side;
    uint64_t cells;
    uint64_t active_cells;
    uint64_t transition_count;
    uint64_t transition_bytes;
    uint64_t value_bytes;
    uint64_t choice_bytes;
    uint64_t state_bytes;
    uint64_t tile_cells;
    uint64_t tile_payload_bytes;
    uint64_t estimated_visits;
    bool estimated_visits_overflow;
} kh_resource_estimate_t;

/*
 * Parse and validate the paper's prime-power parameters.
 *
 * Parameters:
 *   p: Candidate prime in 2..UINT16_MAX.
 *   r: Candidate odd extension degree in 3..UINT8_MAX.
 *   output: Output structure receiving q, F, and B on success.
 *   error: Output pointer receiving a static diagnostic string on failure.
 *
 * Returns:
 *   True on success; false for composite inputs, unsupported degrees, or q exceeding UINT32_MAX.
 */
bool kh_parameters(uint32_t p, uint32_t r, kh_parameters_t *output, const char **error);

/*
 * Compute checked dense-DP resource counts.
 *
 * Parameters:
 *   parameters: Valid input parameters from kh_parameters.
 *   tile_side: Positive logical tile side in cells.
 *   output: Output structure receiving byte and work estimates.
 *   error: Output pointer receiving a static diagnostic string on overflow.
 *
 * Returns:
 *   True on success; false when a count cannot be represented in uint64_t.
 */
bool kh_estimate_resources(
    const kh_parameters_t *parameters,
    uint32_t tile_side,
    kh_resource_estimate_t *output,
    const char **error
);

/*
 * Parse a decimal uint64_t without accepting signs or suffixes.
 *
 * Parameters:
 *   text: Input NUL-terminated decimal string.
 *   output: Output integer written on success.
 *
 * Returns:
 *   True for a canonical decimal value in range; false otherwise.
 */
bool kh_parse_u64(const char *text, uint64_t *output);

/*
 * Count the distinct residues in the paper's omega(a,b,t) definition.
 *
 * Parameters:
 *   p: Prime modulus.
 *   a: Number of consecutive position-side residues.
 *   b: Number of symbol-side residues.
 *   t: Residue spacing and number of cosets.
 *   scratch: Writable bitset containing at least ceil(p/64) words.
 *
 * Returns:
 *   The number of distinct residues h*t-g modulo p.
 */
uint32_t kh_omega(
    uint16_t p,
    uint16_t a,
    uint16_t b,
    uint16_t t,
    uint64_t *scratch
);

// Owned scan array and exact identical-cost reduction statistics.
typedef struct {
    kh_transition_t *entries;
    uint32_t raw_count;
    uint32_t count;
    uint32_t lower_gain_removed;
    uint32_t equal_gain_removed;
    uint64_t array_peak_bytes; // Original array payload, excluding sort workspace and RSS.
    uint64_t array_bytes; // Allocated payload after the optional shrink.
} kh_transition_table_t;

/*
 * Build raw transitions or keep the earliest greatest-gain choice per cost pair.
 * Parameters:
 *   parameters: Validated dimensions; p^3 must fit a uint32_t choice ID.
 *   reduce: Select identical-cost reduction, preserving original a,b,t order.
 *   output: Receives an owned array and counts; free entries after use.
 *   error: Receives a static diagnostic on failure.
 * Returns: True on success; false on size or allocation failure, owning no array.
 */
bool kh_build_transitions(
    const kh_parameters_t *parameters,
    bool reduce,
    kh_transition_table_t *output,
    const char **error
);

/*
 * Encode a triple using the historical one-based raw scan identifier.
 * Parameters:
 *   p: Valid prime modulus.
 *   transition: Input triple with coordinates in 1..p and p^3 <= UINT32_MAX.
 * Returns: Stable choice ID; independent of scan reduction.
 */
static inline uint32_t kh_transition_id(uint16_t p, const kh_transition_t *transition) {
    return ((uint32_t)(transition->a - 1) * p + transition->b - 1) * p + transition->t;
}

/*
 * Decode a historical choice ID without needing a raw transition array.
 * Parameters:
 *   p: Valid prime modulus.
 *   id: One-based original ID; zero is the empty choice.
 *   output: Receives a,b,t on success; gain and reserved are zeroed.
 * Returns: True for a valid triple; false for zero or an out-of-range identifier.
 */
bool kh_decode_transition(uint16_t p, uint32_t id, kh_transition_t *output);

#endif
