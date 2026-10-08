#include "kh_solver.h"

#include <errno.h>
#include <limits.h>
#include <stdlib.h>

_Static_assert(sizeof(kh_transition_t) == 12, "transition layout must remain 12 bytes");

/*
 * Multiply two uint64_t values with overflow detection.
 *
 * Parameters:
 *   left: First input factor.
 *   right: Second input factor.
 *   output: Output product written on success.
 *
 * Returns:
 *   True when the product is representable; false on overflow.
 */
static bool multiply_u64(uint64_t left, uint64_t right, uint64_t *output) {

    // Reject the multiplication before evaluating an overflowing expression.
    if (right != 0 && left > UINT64_MAX / right) {
        return false;
    }

    // Store the checked product for the caller.
    *output = left * right;
    return true;
}

/*
 * Determine whether a uint32_t value is prime by trial division.
 *
 * Parameters:
 *   value: Candidate integer.
 *
 * Returns:
 *   True exactly when value is prime.
 */
static bool is_prime(uint32_t value) {

    // Values below two are not prime.
    if (value < 2) {
        return false;
    }

    // Test divisors only while their square can be at most the candidate.
    for (uint32_t divisor = 2; divisor <= value / divisor; ++divisor) {

        // A proper divisor proves compositeness.
        if (value % divisor == 0) {
            return false;
        }
    }
    return true;
}

/*
 * Parse and validate the paper's prime-power parameters.
 *
 * Parameters:
 *   p: Candidate prime in 2..UINT16_MAX.
 *   r: Candidate odd extension degree in 3..63 (q must also fit 64 bits).
 *   output: Output structure receiving q, F, and B on success.
 *   error: Output pointer receiving a static diagnostic string on failure.
 *
 * Returns:
 *   True on success; false for composite inputs or dimensions exceeding DP widths.
 */
bool kh_parameters_dp64(uint32_t p, uint32_t r, kh_parameters_t *output, const char **error) {
    if (p > UINT16_MAX || (uint64_t)p * p * p > UINT32_MAX) {
        *error = "p or p^3 exceeds DP choice width";
        return false;
    }
    if (!is_prime(p)) {
        *error = "p must be prime";
        return false;
    }
    // r itself is only bounded by q < 2^64 and the budget's width, both checked below; 31 was an
    // arbitrary cap (2026-10-07: 2^33 and 2^35 are cheap fields).
    if (r < 3 || !(r & 1) || r > 63) {
        *error = "r must be an odd integer in 3..63";
        return false;
    }
    uint64_t q = 1;
    uint32_t f = 1;
    for (uint32_t exponent = 0; exponent < r; ++exponent) {
        if (q > UINT64_MAX / p) {
            *error = "p^r exceeds UINT64_MAX";
            return false;
        }
        q *= p;
        if (exponent < r / 2) {
            if (f > UINT32_MAX / p) {
                *error = "F exceeds UINT32_MAX";
                return false;
            }
            f *= p;
        }
    }
    if (f > UINT32_MAX / p) {
        *error = "DP budget exceeds UINT32_MAX";
        return false;
    }
    output->p = (uint16_t)p;
    output->r = (uint8_t)r;
    output->q = q;
    output->f = f;
    output->budget = p * f;
    return true;
}

bool kh_parameters(uint32_t p, uint32_t r, kh_parameters_t *output, const char **error) {
    if (!kh_parameters_dp64(p, r, output, error)) {
        return false;
    }
    if (output->q > UINT32_MAX) {
        *error = "p^r exceeds UINT32_MAX";
        return false;
    }
    return true;
}

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
) {

    // A zero tile has no operational meaning.
    if (tile_side == 0) {
        *error = "tile side must be positive";
        return false;
    }
    uint64_t side = (uint64_t)parameters->budget + 1;
    uint64_t cells;
    uint64_t active_cells;
    uint64_t transitions;
    uint64_t tile_cells;

    // Check every quadratic or cubic dimension before storing it.
    if (!multiply_u64(side, side, &cells) ||
        !multiply_u64(parameters->budget, parameters->budget, &active_cells) ||
        !multiply_u64(parameters->p, parameters->p, &transitions) ||
        !multiply_u64(transitions, parameters->p, &transitions) ||
        !multiply_u64(tile_side, tile_side, &tile_cells)) {
        *error = "resource dimension exceeds uint64_t";
        return false;
    }
    uint64_t value_bytes;
    uint64_t choice_bytes;
    uint64_t transition_bytes;
    uint64_t tile_payload_bytes;
    uint64_t estimated_visits;

    // Convert counts to byte and visit estimates with checked products.
    if (!multiply_u64(cells, sizeof(uint64_t), &value_bytes) ||
        !multiply_u64(cells, sizeof(uint32_t), &choice_bytes) ||
        !multiply_u64(transitions, sizeof(kh_transition_t), &transition_bytes) ||
        !multiply_u64(tile_cells, sizeof(uint64_t) + sizeof(uint32_t), &tile_payload_bytes)) {
        *error = "resource byte estimate exceeds uint64_t";
        return false;
    }

    // Preserve representable resources even when the naive work bound saturates.
    bool visits_overflow = !multiply_u64(active_cells, transitions, &estimated_visits);

    if (visits_overflow) {
        estimated_visits = UINT64_MAX;
    }

    // Check the combined state size rather than relying on individual products.
    if (value_bytes > UINT64_MAX - choice_bytes) {
        *error = "combined state bytes exceed uint64_t";
        return false;
    }

    // Assemble the successful estimate without hidden padding assumptions.
    output->side = side;
    output->cells = cells;
    output->active_cells = active_cells;
    output->transition_count = transitions;
    output->transition_bytes = transition_bytes;
    output->value_bytes = value_bytes;
    output->choice_bytes = choice_bytes;
    output->state_bytes = value_bytes + choice_bytes;
    output->tile_cells = tile_cells;
    output->tile_payload_bytes = tile_payload_bytes;
    output->estimated_visits = estimated_visits;
    output->estimated_visits_overflow = visits_overflow;
    return true;
}

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
bool kh_parse_u64(const char *text, uint64_t *output) {

    // Reject empty strings and all nondecimal characters.
    if (text == NULL || text[0] == '\0') {
        return false;
    }

    // Validate syntax before calling the library conversion routine.
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {

        // Signs and whitespace are intentionally not accepted.
        if (*cursor < '0' || *cursor > '9') {
            return false;
        }
    }
    errno = 0;
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 10);

    // Require complete conversion into the destination width.
    if (errno != 0 || end == NULL || *end != '\0') {
        return false;
    }
    *output = (uint64_t)value;
    return true;
}

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
) {
    size_t words = ((size_t)p + 63) / 64;

    // Clear residues left by the preceding transition.
    for (size_t word = 0; word < words; ++word) {
        scratch[word] = 0;
    }

    // Mark every residue difference specified by the paper.
    for (uint32_t g = 0; g < a; ++g) {

        // Compare this consecutive position residue with each spaced symbol residue.
        for (uint32_t h = 0; h < b; ++h) {
            uint32_t residue = (h * (uint32_t)t + p - g) % p;
            scratch[residue / 64] |= UINT64_C(1) << (residue % 64);
        }
    }
    uint32_t count = 0;

    // Count each marked residue exactly once.
    for (size_t word = 0; word < words; ++word) {
        count += (uint32_t)__builtin_popcountll(scratch[word]);
    }
    return count;
}
