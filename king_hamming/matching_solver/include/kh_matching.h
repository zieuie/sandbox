#ifndef KH_MATCHING_H
#define KH_MATCHING_H

#include "kh_field.h"

// A compact run contributes consecutive cosets with the same stripe count.
typedef struct {
    uint64_t first;
    uint32_t coset;
    uint32_t copies;
    uint16_t stripes;
} kh_request_block_t;

// An implicit graph borrows its field and compact request blocks.
typedef struct {
    const kh_field_t *field;
    const kh_request_block_t *blocks;
    uint32_t block_count;
    uint32_t count;
} kh_graph_t;

// Owned matching and search arrays; UINT32_MAX denotes an unmatched endpoint.
typedef struct {
    uint32_t *left;
    uint32_t *right;
    uint32_t *choice;
    uint32_t *distance;
    uint32_t *queue;
    uint32_t *cursor;
    uint32_t *path;
    uint32_t matched;
    uint64_t scans;
    uint64_t phases;
} kh_matching_t;

/*
 * Decode one canonical request from its compact block.
 * Parameters: graph: Borrowed validated graph; u: Left index; coset,cell: Output request coordinates.
 * Returns: No value; fills both output coordinates.
 */
void kh_request(const kh_graph_t *graph, uint32_t u, uint32_t *coset, uint32_t *cell);

/*
 * Decode a request's neighbor without materializing edges.
 * Parameters: graph: Borrowed graph; coset,cell: Decoded request; k: Neighbor index below F.
 * Returns: Right label, with zero fixed and nonzero labels shifted by X^-coset.
 */
uint32_t kh_neighbor(const kh_graph_t *graph, uint32_t coset, uint32_t cell, uint32_t k);

/*
 * Allocate matching and bounded iterative-search arrays.
 * Parameters: graph: Borrowed graph; output: Zero-initialized owned result.
 * Returns: True on success; false after releasing any partial allocation.
 */
bool kh_matching_create(const kh_graph_t *graph, kh_matching_t *output);

// A completed matching phase can be checkpointed before the next phase begins.
typedef bool (*kh_phase_callback_t)(const kh_graph_t *graph, const kh_matching_t *matching,
                                     void *context, const char **error);

/*
 * Solve with an optional callback after each fully committed phase.
 * Parameters: graph: Borrowed graph; matching: Empty or restored valid state; threads: Pinned workers; callback,context: Optional phase hook and borrowed state; error: Diagnostic.
 * Returns: True for a completed maximum matching; false if the hook or worker setup fails.
 */
bool kh_matching_solve_with_hook(const kh_graph_t *graph, kh_matching_t *matching,
                                 uint32_t threads, kh_phase_callback_t callback,
                                 void *context, const char **error);

/*
 * Find an exact maximum matching using iterative Hopcroft-Karp phases.
 * Parameters: graph: Borrowed graph; matching: Allocated empty matching; threads: Pinned BFS worker count; error: Static diagnostic on failure.
 * Returns: True with maximum cardinality and choices; false on worker setup failure. Reports phase progress.
 */
bool kh_matching_solve(const kh_graph_t *graph, kh_matching_t *matching, uint32_t threads, const char **error);

/*
 * Compute alternating closure from all unmatched left vertices.
 * Parameters: graph: Borrowed graph; matching: Maximum matching; left_count,right_count: Output Hall counts.
 * Returns: True for the exact deficiency invariant. distance[u] becomes 1 iff u belongs to the Hall set.
 */
bool kh_matching_hall(const kh_graph_t *graph, kh_matching_t *matching, uint32_t *left_count, uint32_t *right_count);

/*
 * Release all owned matching arrays.
 * Parameters: matching: Owned or partially initialized state.
 * Returns: No value; releases and clears the state.
 */
void kh_matching_free(kh_matching_t *matching);

#endif
