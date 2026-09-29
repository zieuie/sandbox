#include "kh_solver.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Check each raw transition against independently selected same-cost winners.
 * Parameters:
 *   p: Small test prime, keeping the exhaustive pair comparisons inexpensive.
 * Returns: No value; assertion failure identifies a reduction or ID error.
 */
static void check_prime(uint16_t p) {
    kh_parameters_t parameters;
    kh_transition_table_t raw;
    kh_transition_table_t reduced;
    const char *error;
    assert(kh_parameters(p, 3, &parameters, &error));
    assert(kh_build_transitions(&parameters, false, &raw, &error));
    assert(kh_build_transitions(&parameters, true, &reduced, &error));
    uint32_t kept = 0;
    uint32_t lower = 0;
    uint32_t equal = 0;

    // Verify every ID roundtrip and independently find its exact cost-group winner.
    for (uint32_t index = 0; index < raw.count; ++index) {
        kh_transition_t entry = raw.entries[index];
        kh_transition_t decoded;
        assert(kh_transition_id(p, &entry) == index + 1);
        assert(kh_decode_transition(p, index + 1, &decoded));
        assert(decoded.a == entry.a && decoded.b == entry.b && decoded.t == entry.t);
        uint32_t winner = index;

        // Direct exhaustive comparisons are intentionally independent of the sorting algorithm.
        for (uint32_t other = 0; other < raw.count; ++other) {
            kh_transition_t candidate = raw.entries[other];

            // Restrict comparison to exactly equal predecessors.
            if ((uint32_t)candidate.a * candidate.t != (uint32_t)entry.a * entry.t ||
                (uint32_t)candidate.b * candidate.t != (uint32_t)entry.b * entry.t) {
                continue;
            }

            // Choose the greatest gain, then the earliest original identifier.
            if (candidate.gain > raw.entries[winner].gain ||
                (candidate.gain == raw.entries[winner].gain && other < winner)) {
                winner = other;
            }
        }

        // Every winner must appear exactly once in ascending original order.
        if (winner == index) {
            assert(kept < reduced.count);
            assert(memcmp(&entry, &reduced.entries[kept], sizeof entry) == 0);
            ++kept;
        } else if (entry.gain < raw.entries[winner].gain) {

            // Classify strictly inferior records independently.
            ++lower;
        } else {

            // Classify later equal-gain records independently.
            ++equal;
        }
    }
    assert(kept == reduced.count);
    assert(lower == reduced.lower_gain_removed);
    assert(equal == reduced.equal_gain_removed);
    kh_transition_t decoded;
    assert(!kh_decode_transition(p, 0, &decoded));
    assert(!kh_decode_transition(p, raw.count + 1, &decoded));
    free(raw.entries);
    free(reduced.entries);
}

/*
 * Run direct reduction checks or print a useful example.
 * Parameters:
 *   argc: Number of command-line arguments.
 *   argv: Input argument strings.
 * Returns: Zero for help or passing checks; one for invalid arguments.
 */
int main(int argc, char **argv) {

    // An empty invocation displays the test utility's interface.
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        puts("Check exact transition reduction and stable choice identifiers.\n"
             "Usage: ./build/transitions_unit --run\n"
             "Example: ./build/transitions_unit --run");
        return 0;
    }

    // Require an explicit request before running exhaustive comparisons.
    if (argc != 2 || strcmp(argv[1], "--run")) {
        return 1;
    }
    const uint16_t primes[] = {2, 3, 5, 7, 11, 17};

    // Cover both binary and odd-prime fields.
    for (size_t index = 0; index < sizeof primes / sizeof primes[0]; ++index) {
        check_prime(primes[index]);
    }
    puts("transition checks passed");
    return 0;
}
