#include "kh_solver.h"

#include <stdlib.h>

/*
 * Recover a triple from a stable choice ID.
 * Parameters:
 *   p: Valid prime.
 *   id: One-based original choice ID.
 *   output: Receives a,b,t and zeroed auxiliary fields on success.
 * Returns: True on a valid ID; false otherwise.
 */
bool kh_decode_transition(uint16_t p, uint32_t id, kh_transition_t *output) {

    // Check in wide arithmetic before decoding the mixed-radix representation.
    if (p < 2 || id == 0 || id > (uint64_t)p * p * p) {
        return false;
    }
    uint32_t index = id - 1;
    *output = (kh_transition_t){0};
    output->t = (uint16_t)(index % p + 1);
    index /= p;
    output->b = (uint16_t)(index % p + 1);
    output->a = (uint16_t)(index / p + 1);
    return true;
}

/*
 * Compare original a,b,t tie order without subtraction overflow.
 * Parameters:
 *   left: Input kh_transition_t record.
 *   right: Input kh_transition_t record.
 * Returns: Negative, zero, or positive according to original scan order.
 */
static int compare_order(const void *left, const void *right) {
    const kh_transition_t *a = left;
    const kh_transition_t *b = right;

    // Lexicographic triple order is the original identifier order.
    if (a->a != b->a) {
        return a->a < b->a ? -1 : 1;
    }

    // Resolve the second coordinate before the spacing coordinate.
    if (a->b != b->b) {
        return a->b < b->b ? -1 : 1;
    }
    return (a->t > b->t) - (a->t < b->t);
}

/*
 * Compare costs, descending gain, then original order within a cost group.
 * Parameters:
 *   left: Input kh_transition_t record.
 *   right: Input kh_transition_t record.
 * Returns: Negative, zero, or positive for the grouping sort.
 */
static int compare_costs(const void *left, const void *right) {
    const kh_transition_t *a = left;
    const kh_transition_t *b = right;
    uint32_t au = (uint32_t)a->a * a->t;
    uint32_t bu = (uint32_t)b->a * b->t;
    uint32_t av = (uint32_t)a->b * a->t;
    uint32_t bv = (uint32_t)b->b * b->t;

    // Contiguous equal-cost groups share both affordability and predecessor.
    if (au != bu) {
        return au < bu ? -1 : 1;
    }

    // Group by the second budget cost as well.
    if (av != bv) {
        return av < bv ? -1 : 1;
    }

    // The first member is the greatest gain, breaking equal gains by raw order.
    if (a->gain != b->gain) {
        return a->gain > b->gain ? -1 : 1;
    }
    return compare_order(left, right);
}

/*
 * Enumerate transitions and optionally compact each identical-cost group.
 * Parameters:
 *   parameters: Validated prime-power dimensions.
 *   reduce: Whether to retain only exact cost-group winners.
 *   output: Receives owned entries and statistics; caller frees entries.
 *   error: Receives a static size or allocation diagnostic on failure.
 * Returns: True on success; false with no owned array on failure.
 */
bool kh_build_transitions(
    const kh_parameters_t *parameters,
    bool reduce,
    kh_transition_table_t *output,
    const char **error
) {
    uint32_t p = parameters->p;
    uint64_t raw_count = (uint64_t)p * p * p;
    *output = (kh_transition_table_t){0};

    // Bound raw choice IDs and allocation size before enumeration.
    if (raw_count > UINT32_MAX || raw_count > SIZE_MAX / sizeof(kh_transition_t)) {
        *error = "transition table exceeds choice ID or allocation limits";
        return false;
    }
    kh_transition_t *entries = calloc((size_t)raw_count, sizeof *entries);
    size_t words = ((size_t)p + 63) / 64;
    uint64_t *scratch = calloc(words, sizeof *scratch);

    // Free any successful allocation when its companion failed.
    if (entries == NULL || scratch == NULL) {
        free(entries);
        free(scratch);
        *error = "cannot allocate transition table";
        return false;
    }
    uint32_t index = 0;

    // Enumerate the original tie order before any reduction.
    for (uint32_t a = 1; a <= p; ++a) {

        // Enumerate every symbol-side width.
        for (uint32_t b = 1; b <= p; ++b) {

            // Enumerate every spacing, including t=p as in the reference.
            for (uint32_t t = 1; t <= p; ++t) {
                kh_transition_t *entry = &entries[index++];
                entry->a = (uint16_t)a;
                entry->b = (uint16_t)b;
                entry->t = (uint16_t)t;
                entry->gain = t * kh_omega((uint16_t)p, entry->a, entry->b, entry->t, scratch);
            }
        }
    }
    free(scratch);
    output->entries = entries;
    output->raw_count = (uint32_t)raw_count;
    output->count = (uint32_t)raw_count;
    output->array_peak_bytes = raw_count * sizeof *entries;
    output->array_bytes = output->array_peak_bytes;

    // The reference mode keeps all transitions without any sorting.
    if (!reduce) {
        return true;
    }

    // Place the greatest gain and earliest original record first in each cost group.
    qsort(entries, (size_t)raw_count, sizeof *entries, compare_costs);
    uint32_t retained = 0;
    index = 0;

    // Compact in place; the first sorted record is the exact group winner.
    while (index < raw_count) {
        kh_transition_t winner = entries[index++];
        uint32_t du = (uint32_t)winner.a * winner.t;
        uint32_t dv = (uint32_t)winner.b * winner.t;

        // Classify only identical costs; no cross-cost dominance is assumed.
        while (index < raw_count &&
               (uint32_t)entries[index].a * entries[index].t == du &&
               (uint32_t)entries[index].b * entries[index].t == dv) {

            // Equal-gain losers are later in the original tie order.
            if (entries[index].gain == winner.gain) {
                ++output->equal_gain_removed;
            } else {

                // Strictly inferior gains can never maximize this predecessor.
                ++output->lower_gain_removed;
            }
            ++index;
        }
        entries[retained++] = winner;
    }

    // Restore cross-group tie order before exposing the compact scan array.
    qsort(entries, retained, sizeof *entries, compare_order);
    output->count = retained;
    kh_transition_t *compact = realloc(entries, (size_t)retained * sizeof *entries);

    // A failed optional shrink preserves the correct array and reports its allocation.
    if (compact != NULL) {
        output->entries = compact;
        output->array_bytes = (uint64_t)retained * sizeof *entries;
    }
    return true;
}
