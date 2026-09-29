#include "kh_matching.h"
#include "kh_threads.h"

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

/*
 * Exercise an exact Hall obstruction on an intentionally deficient synthetic graph.
 * Parameters: argc: Argument count; argv: Input strings; --run performs the test.
 * Returns: Zero for help/success; one for a failed invariant.
 */
int main(int argc, char **argv) {

    // Tests must remain explicit when invoked without arguments.
    if (argc != 2 || argv[1][0] != '-' || argv[1][1] != '-' || argv[1][2] != 'r') {
        puts("Check iterative matching and Hall closure on a synthetic deficient graph.\n"
             "Usage: ./matching_unit --run\n"
             "Example: ./matching_unit --run");
        return 0;
    }
    kh_field_t field = {0};
    uint32_t cells[8] = {0, 0, 0, 0, 4, 5, 6, 7};
    kh_request_block_t block = {0, 0, 1, 1};
    kh_graph_t graph = {0};
    kh_matching_t matching = {0};
    uint32_t left_count = 0;
    uint32_t right_count = 0;
    field.parameters.p = 2;
    field.parameters.r = 3;
    field.parameters.q = 8;
    field.parameters.f = 2;
    field.cells = cells;
    graph.field = &field;
    graph.blocks = &block;
    graph.block_count = 1;
    graph.count = 2;

    // Both left vertices reach only zero in this deliberately non-field test fixture.
    if (!kh_matching_create(&graph, &matching)) {
        return 1;
    }
    const char *error = NULL;
    bool solved = kh_matching_solve(&graph, &matching, 1, &error);
    bool valid = solved && matching.matched == 1 && kh_matching_hall(&graph, &matching, &left_count, &right_count) &&
                 left_count == 2 && right_count == 1 && matching.distance[0] == 1 && matching.distance[1] == 1;
    kh_matching_free(&matching);

    // A larger synthetic closure launches more than one BFS worker when CPUs permit.
    uint32_t large_cells[128] = {0};
    kh_field_t large_field = {0};
    kh_request_block_t large_block = {0, 0, 64, 1};
    kh_graph_t large_graph = {0};
    kh_matching_t large_matching = {0};
    int cpus[2];
    uint32_t workers = kh_select_cpus(2, cpus, &error) ? 2 : 1;
    large_field.parameters.p = 2;
    large_field.parameters.r = 7;
    large_field.parameters.q = 128;
    large_field.parameters.f = 2;
    large_field.cells = large_cells;
    large_graph.field = &large_field;
    large_graph.blocks = &large_block;
    large_graph.block_count = 1;
    large_graph.count = 128;

    // Every canonical request has only the same zero endpoint in this fixture.
    if (!kh_matching_create(&large_graph, &large_matching)) {
        return 1;
    }
    bool large_solved = kh_matching_solve(&large_graph, &large_matching, workers, &error);
    bool large_hall = large_solved && large_matching.matched == 1 &&
        kh_matching_hall(&large_graph, &large_matching, &left_count, &right_count) &&
        left_count == 128 && right_count == 1;
    kh_matching_free(&large_matching);
    return valid && large_hall ? 0 : 1;
}
