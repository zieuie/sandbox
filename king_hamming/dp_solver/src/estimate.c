#include "kh_solver.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Print estimator usage and a runnable command.
 *
 * Parameters: none.
 *
 * Returns:
 *   No value; writes help to stdout.
 */
static void help(void) {
    puts("Estimate production DP resources for an odd prime power.\n"
         "Usage: ./kh_estimate PRIME ODD_DEGREE [--tile-side CELLS] [--json]\n"
         "Optional: --profile-transitions [--max-profile-transitions N]\n"
         "Example: ./kh_estimate 7 5 --tile-side 4096 --json\n"
         "Supports q=p^r <= UINT64_MAX with a uint32 DP budget; estimates may exceed practical resources.\n"
         "No arguments or --help prints this help.");
}

/*
 * Report invalid input and return a command failure.
 *
 * Parameters:
 *   message: Input diagnostic text.
 *
 * Returns:
 *   Exit status 1 after writing the diagnostic to stderr.
 */
static int fail(const char *message) {
    fprintf(stderr, "kh_estimate: %s\n", message);
    return 1;
}

/*
 * Parse arguments, calculate resource counts, and print them.
 *
 * Parameters:
 *   argc: Number of command-line arguments.
 *   argv: Input argument vector.
 *
 * Returns:
 *   Zero for help or a valid estimate; one for invalid input.
 */
int main(int argc, char **argv) {

    // Treat no arguments as a successful help request.
    if (argc == 1 || (argc == 2 && (!strcmp(argv[1], "--help") || !strcmp(argv[1], "-h")))) {
        help();
        return 0;
    }

    // Require the two mathematical arguments before parsing options.
    if (argc < 3) {
        help();
        return 1;
    }
    uint64_t parsed_p;
    uint64_t parsed_r;

    // Parse the prime and degree into checked public widths.
    if (!kh_parse_u64(argv[1], &parsed_p) || parsed_p > UINT32_MAX ||
        !kh_parse_u64(argv[2], &parsed_r) || parsed_r > UINT32_MAX) {
        return fail("invalid prime or degree");
    }
    uint32_t tile_side = 4096;
    bool json = false;
    bool profile = false;
    uint64_t max_profile_transitions = 100000;

    // Parse optional output and tile controls.
    for (int index = 3; index < argc; ++index) {

        // Select machine-readable output without changing the estimate.
        if (!strcmp(argv[index], "--json")) {
            json = true;
            continue;
        }

        // Enumerate cost groups only when explicitly requested.
        if (!strcmp(argv[index], "--profile-transitions")) {
            profile = true;
            continue;
        }

        // Bound enumeration separately from the cheap full-range estimator.
        if (!strcmp(argv[index], "--max-profile-transitions") && index + 1 < argc) {
            if (!kh_parse_u64(argv[++index], &max_profile_transitions) ||
                max_profile_transitions == 0) {
                return fail("invalid profile transition limit");
            }
            continue;
        }

        // Read the tile side from the following argument.
        if (!strcmp(argv[index], "--tile-side") && index + 1 < argc) {
            uint64_t value;

            // Require a positive uint32_t tile side.
            if (!kh_parse_u64(argv[++index], &value) || value == 0 || value > UINT32_MAX) {
                return fail("invalid tile side");
            }
            tile_side = (uint32_t)value;
            continue;
        }
        return fail("unknown or incomplete option");
    }
    kh_parameters_t parameters;
    kh_resource_estimate_t estimate;
    const char *error;

    // Derive dimensions and all checked byte counts.
    if (!kh_parameters_dp64((uint32_t)parsed_p, (uint32_t)parsed_r, &parameters, &error) ||
        !kh_estimate_resources(&parameters, tile_side, &estimate, &error)) {
        return fail(error);
    }

    kh_transition_table_t table = {0};

    // Keep enormous representable fields cheap to estimate and safe to profile.
    if (profile) {
        if (estimate.transition_count > max_profile_transitions) {
            return fail("profile enumeration exceeds --max-profile-transitions");
        }

        // Reuse the exact reduction used by the solver, without allocating DP state.
        if (!kh_build_transitions(&parameters, true, &table, &error)) {
            return fail(error);
        }
    }

    // Emit stable keys for the scheduler and benchmark collector.
    if (json) {
        printf("{\"p\":%u,\"r\":%u,\"q\":%" PRIu64
               ",\"f\":%" PRIu32 ",\"budget\":%" PRIu32
               ",\"side\":%" PRIu64 ",\"cells\":%" PRIu64
               ",\"transitions\":%" PRIu64 ",\"transition_bytes\":%" PRIu64
               ",\"value_bytes\":%" PRIu64 ",\"choice_bytes\":%" PRIu64
               ",\"state_bytes\":%" PRIu64 ",\"tile_side\":%u"
               ",\"tile_payload_bytes\":%" PRIu64
               ",\"estimated_visits\":%" PRIu64 ",\"estimated_visits_overflow\":%s",
               parameters.p,
               parameters.r,
               parameters.q,
               parameters.f,
               parameters.budget,
               estimate.side,
               estimate.cells,
               estimate.transition_count,
               estimate.transition_bytes,
               estimate.value_bytes,
               estimate.choice_bytes,
               estimate.state_bytes,
               tile_side,
               estimate.tile_payload_bytes,
               estimate.estimated_visits,
               estimate.estimated_visits_overflow ? "true" : "false");

        // Add optional counts without changing the default estimator keys.
        if (profile) {
            bool visits_overflow = estimate.active_cells > UINT64_MAX / table.count;
            uint64_t visits = visits_overflow ? UINT64_MAX : estimate.active_cells * table.count;
            printf(",\"transition_profile\":{\"raw_count\":%u,\"cost_groups\":%u,"
                   "\"lower_gain_removed\":%u,\"equal_gain_removed\":%u,"
                   "\"scan_bytes\":%" PRIu64 ",\"array_peak_bytes\":%" PRIu64 ","
                   "\"array_bytes\":%" PRIu64 ",\"scan_visits\":%" PRIu64 ",\"scan_visits_overflow\":%s}",
                   table.raw_count,
                   table.count,
                   table.lower_gain_removed,
                   table.equal_gain_removed,
                   (uint64_t)table.count * sizeof(kh_transition_t),
                   table.array_peak_bytes,
                   table.array_bytes,
                   visits,
                   visits_overflow ? "true" : "false");
        }
        puts("}");
    } else {

        // Keep the default report easy to inspect by hand.
        printf("field: %u^%u = %" PRIu64 "\n", parameters.p, parameters.r, parameters.q);
        printf("F: %" PRIu32 "\n", parameters.f);
        printf("budget B: %" PRIu32 "\n", parameters.budget);
        printf("DP side/cells: %" PRIu64 " / %" PRIu64 "\n", estimate.side, estimate.cells);
        printf("transitions: %" PRIu64 " (%" PRIu64 " bytes)\n",
               estimate.transition_count,
               estimate.transition_bytes);
        printf("state: %" PRIu64 " bytes (values=%" PRIu64 ", choices=%" PRIu64 ")\n",
               estimate.state_bytes,
               estimate.value_bytes,
               estimate.choice_bytes);
        printf("tile: %u x %u, payload=%" PRIu64 " bytes\n",
               tile_side,
               tile_side,
               estimate.tile_payload_bytes);
        if (estimate.estimated_visits_overflow) {
            puts("upper-bound transition visits: exceeds uint64_t");
        } else {

            // Print the exact bound when it remains representable.
            printf("upper-bound transition visits: %" PRIu64 "\n", estimate.estimated_visits);
        }
    }
    // Report reduced scan counts alongside the conservative raw resource bound.
    if (profile && !json) {
        printf("identical-cost groups: %u / %u transitions\n", table.count, table.raw_count);
        printf("removed: %u lower gain, %u equal gain\n",
               table.lower_gain_removed, table.equal_gain_removed);
        printf("transition array: %" PRIu64 " allocated, %" PRIu64 " peak payload bytes\n",
               table.array_bytes, table.array_peak_bytes);
    }
    free(table.entries);
    return 0;
}
