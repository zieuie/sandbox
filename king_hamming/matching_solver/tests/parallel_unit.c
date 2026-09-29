#include "kh_matching.h"
#include "kh_threads.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

/*
 * Produce repeatable graph labels without depending on libc random state.
 * Parameters: state: Mutable nonzero generator state.
 * Returns: Next 32-bit pseudorandom word.
 */
static uint32_t random_word(uint32_t *state) {
    uint32_t value = *state;
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    *state = value;
    return value;
}

/*
 * Find one augmenting edge chain for the independent reference matcher.
 * Parameters: graph: Tiny implicit graph; u: Left vertex; owner: Mutable right owners; seen: Right visits for this search.
 * Returns: True after extending the reference matching; false when no free right is reachable.
 */
static bool reference_augment(const kh_graph_t *graph, uint32_t u, uint32_t *owner, bool *seen) {
    uint32_t coset;
    uint32_t cell;
    kh_request(graph, u, &coset, &cell);

    // The reference search uses right visits, not the production BFS or path claims.
    for (uint32_t k = 0; k < graph->field->parameters.f; ++k) {
        uint32_t v = kh_neighbor(graph, coset, cell, k);

        // Each right vertex is considered at most once per root search.
        if (seen[v]) {
            continue;
        }
        seen[v] = true;

        // Reassign the current owner recursively when a free endpoint is reachable.
        if (owner[v] == UINT32_MAX || reference_augment(graph, owner[v], owner, seen)) {
            owner[v] = u;
            return true;
        }
    }
    return false;
}

/*
 * Compute an exact maximum cardinality with a different augmenting algorithm.
 * Parameters: graph: Tiny graph with at most 128 right vertices.
 * Returns: Maximum number of covered left vertices.
 */
static uint32_t reference_maximum(const kh_graph_t *graph) {
    uint32_t owner[128];
    uint32_t matched = 0;

    // Every right endpoint begins free in the independent reference.
    for (uint32_t v = 0; v < graph->field->parameters.q; ++v) {
        owner[v] = UINT32_MAX;
    }

    // A fresh right-visited array gives each root one exact augmenting search.
    for (uint32_t u = 0; u < graph->count; ++u) {
        bool seen[128] = {0};

        // Exhaustive right-reachable search proves the reference cardinality.
        if (reference_augment(graph, u, owner, seen)) {
            ++matched;
        }
    }
    return matched;
}

/*
 * Verify every selected edge, distinct right endpoint, and optional Hall witness.
 * Parameters: graph: Tiny graph; matching: Completed production matching; expected: Independent maximum.
 * Returns: True only for a valid maximum matching and exact obstruction certificate.
 */
static bool validate_matching(const kh_graph_t *graph, kh_matching_t *matching, uint32_t expected) {
    bool used[128] = {0};
    uint32_t counted = 0;

    // The production choice must decode to its claimed unique right label.
    for (uint32_t u = 0; u < graph->count; ++u) {
        uint32_t v = matching->left[u];

        // Unmatched requests remain free in an exact partial matching.
        if (v == UINT32_MAX) {
            continue;
        }
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);

        // Both pairing directions and the compact choice must agree.
        if (v >= graph->field->parameters.q || used[v] || matching->right[v] != u ||
            matching->choice[u] >= graph->field->parameters.f ||
            kh_neighbor(graph, coset, cell, matching->choice[u]) != v) {
            return false;
        }
        used[v] = true;
        ++counted;
    }

    // A full matching needs no Hall obstruction; a partial maximum requires one.
    if (counted != expected || counted != matching->matched) {
        return false;
    }
    if (counted < graph->count) {
        uint32_t left_count = 0;
        uint32_t right_count = 0;
        return kh_matching_hall(graph, matching, &left_count, &right_count) &&
               left_count > right_count && left_count - right_count == graph->count - counted;
    }
    return true;
}

/*
 * Compare serial and parallel matching with an independent exact reference on many graphs.
 * Parameters: argc: CLI count; argv: --run requests the test.
 * Returns: Zero for help/passing tests; one after the first failed invariant.
 */
int main(int argc, char **argv) {

    // Preserve useful no-argument help even for this internal test executable.
    if (argc != 2 || strcmp(argv[1], "--run") != 0) {
        puts("Compare serial and parallel maximum matching on deterministic small graphs.\n"
             "Usage: ./tests/parallel_unit --run\n"
             "Example: ./tests/parallel_unit --run");
        return 0;
    }
    int cpus[2];
    const char *error = NULL;
    uint32_t threads = kh_select_cpus(2, cpus, &error) ? 2 : 1;
    uint32_t seed = UINT32_C(0x91e10da5);

    // Test both short and genuinely concurrent frontiers, with arbitrary repeated neighbors.
    for (uint32_t size = 8; size <= 128; size *= 16) {
        uint32_t cells[128];
        kh_field_t field = {0};
        kh_request_block_t block = {0};
        kh_graph_t graph = {0};
        field.parameters.p = 2;
        field.parameters.r = size == 8 ? 3 : 7;
        field.parameters.q = size;
        field.parameters.f = 2;
        field.cells = cells;
        block.first = 0;
        block.coset = 0;
        block.copies = size / 2;
        block.stripes = 1;
        graph.field = &field;
        graph.blocks = &block;
        graph.block_count = 1;
        graph.count = size;

        // Independent random tables vary collisions and layered path lengths.
        for (uint32_t example = 0; example < 64; ++example) {

            // Every cell entry is a valid right label, though this test need not form a field.
            for (uint32_t index = 0; index < size; ++index) {
                cells[index] = random_word(&seed) % size;
            }
            uint32_t expected = reference_maximum(&graph);

            // Serial and pinned parallel execution must both attain the independent maximum.
            for (uint32_t mode = 0; mode < 2; ++mode) {
                kh_matching_t matching = {0};

                // A failed allocation or worker launch is a test failure, never a matching.
                if (!kh_matching_create(&graph, &matching)) {
                    return 1;
                }
                bool solved = kh_matching_solve(&graph, &matching, mode == 0 ? 1 : threads, &error);
                bool valid = solved && validate_matching(&graph, &matching, expected);
                kh_matching_free(&matching);

                // Report the exact fixture for a reproducible debugging case.
                if (!valid) {
                    fprintf(stderr, "parallel_unit: size=%u example=%u mode=%u expected=%u error=%s\n",
                            size, example, mode, expected, error == NULL ? "none" : error);
                    return 1;
                }
            }
        }
    }
    puts("serial and parallel maximum-matching checks passed");
    return 0;
}
