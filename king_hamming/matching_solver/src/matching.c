#define _GNU_SOURCE

#include "kh_matching.h"
#include "kh_threads.h"

#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Decode a request by binary-searching compact block boundaries.
 * Parameters: graph: Valid graph; u: Left index; coset,cell: Output coordinates.
 * Returns: No value; writes the request's coset and SUD cell.
 */
void kh_request(const kh_graph_t *graph, uint32_t u, uint32_t *coset, uint32_t *cell) {
    uint32_t low = 0;
    uint32_t high = graph->block_count;

    // Find the last block whose first request does not exceed u.
    while (low + 1 < high) {
        uint32_t middle = low + (high - low) / 2;

        // Sorted starts delimit every request without expanded vertex records.
        if (graph->blocks[middle].first <= u) {
            low = middle;
        } else {
            high = middle;
        }
    }
    const kh_request_block_t *block = &graph->blocks[low];
    uint64_t offset = u - block->first;
    uint32_t width = (uint32_t)block->stripes * graph->field->parameters.f;
    *coset = block->coset + (uint32_t)(offset / width);
    *cell = (uint32_t)(offset % width);
}

/*
 * Shift a canonical cell entry using wide modular exponent arithmetic.
 * Parameters: graph: Valid graph; coset,cell: Request coordinates; k: Choice below F.
 * Returns: The corresponding right label in 0..q-1.
 */
uint32_t kh_neighbor(const kh_graph_t *graph, uint32_t coset, uint32_t cell, uint32_t k) {
    uint32_t f = graph->field->parameters.f;
    uint32_t q = graph->field->parameters.q;
    uint32_t label = graph->field->cells[(uint64_t)cell * f + k];

    // Multiplication leaves the zero element fixed.
    if (label == 0) {
        return 0;
    }
    return 1 + (uint32_t)(((uint64_t)label - 1 + q - 1 - coset) % (q - 1));
}

/*
 * Release matching arrays after completion or partial allocation failure.
 * Parameters: matching: Owned state, possibly partially allocated.
 * Returns: No value; all pointers and counters become zero.
 */
void kh_matching_free(kh_matching_t *matching) {
    free(matching->left);
    free(matching->right);
    free(matching->choice);
    free(matching->distance);
    free(matching->queue);
    free(matching->cursor);
    free(matching->path);
    memset(matching, 0, sizeof *matching);
}

/*
 * Allocate six left arrays and one right array with checked sizes.
 * Parameters: graph: Valid dimensions; output: Zero-initialized owned state.
 * Returns: True on success; false with no remaining owned storage.
 */
bool kh_matching_create(const kh_graph_t *graph, kh_matching_t *output) {
    uint64_t n = graph->count;
    uint64_t q = graph->field->parameters.q;

    // Prevent truncation of byte counts on a narrower size_t platform.
    if (n > SIZE_MAX / sizeof(uint32_t) || q > SIZE_MAX / sizeof(uint32_t)) {
        return false;
    }
    output->left = malloc((size_t)n * sizeof(uint32_t));
    output->right = malloc((size_t)q * sizeof(uint32_t));
    output->choice = malloc((size_t)n * sizeof(uint32_t));
    output->distance = malloc((size_t)n * sizeof(uint32_t));
    output->queue = malloc((size_t)n * sizeof(uint32_t));
    output->cursor = malloc((size_t)n * sizeof(uint32_t));
    output->path = malloc((size_t)n * sizeof(uint32_t));

    // The caller receives ownership only after every allocation succeeds.
    if (output->left == NULL || output->right == NULL || output->choice == NULL ||
        output->distance == NULL || output->queue == NULL || output->cursor == NULL || output->path == NULL) {
        kh_matching_free(output);
        return false;
    }
    memset(output->left, 255, (size_t)n * sizeof(uint32_t));
    memset(output->right, 255, (size_t)q * sizeof(uint32_t));
    return true;
}

/*
 * Build shortest alternating-path levels from all unmatched requests.
 * Parameters: graph: Borrowed graph; matching: Search scratch and current assignment.
 * Returns: Shortest distance to a free right endpoint, or UINT32_MAX if none exists.
 */
static uint32_t levels(const kh_graph_t *graph, kh_matching_t *matching) {
    uint64_t head = 0;
    uint64_t tail = 0;
    uint32_t shortest = UINT32_MAX;

    // Every free left endpoint begins at level zero.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        matching->distance[u] = UINT32_MAX;
        matching->cursor[u] = 0;

        // A request enters the BFS queue at most once per phase.
        if (matching->left[u] == UINT32_MAX) {
            matching->distance[u] = 0;
            matching->queue[tail++] = u;
        }
    }

    // Stop expansion beyond the shortest free-right layer.
    while (head < tail) {
        uint32_t u = matching->queue[head++];
        uint32_t distance = matching->distance[u];

        // A completed shortest layer cannot lead to useful deeper paths.
        if (distance >= shortest) {
            continue;
        }
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);

        // Edges remain implicit throughout level construction.
        for (uint32_t k = 0; k < graph->field->parameters.f; ++k) {
            uint32_t v = kh_neighbor(graph, coset, cell, k);
            uint32_t next = matching->right[v];
            ++matching->scans;

            // A free right endpoint establishes the shortest augmenting distance.
            if (next == UINT32_MAX) {
                shortest = distance + 1;
            } else if (matching->distance[next] == UINT32_MAX) {
                matching->distance[next] = distance + 1;
                matching->queue[tail++] = next;
            }
        }
    }
    return shortest;
}

// One BFS level shares immutable assignments and disjoint queues across workers.
typedef struct {
    const kh_graph_t *graph;
    const kh_matching_t *matching;
    const uint32_t *frontier;
    uint32_t *next;
    uint64_t count;
    uint32_t depth;
    _Atomic uint64_t index;
    _Atomic uint64_t tail;
    _Atomic uint64_t scans;
    _Atomic bool found;
} kh_bfs_level_t;

/*
 * Scan disjoint frontier chunks and claim unseen matched-left endpoints atomically.
 * Parameters: argument: Borrowed kh_bfs_level_t shared by workers in this one layer.
 * Returns: NULL after publishing discovered endpoints and scan count.
 */
static void *scan_frontier(void *argument) {
    kh_bfs_level_t *level = argument;
    uint64_t scans = 0;

    // Static assignment would imbalance high-degree or atypical request blocks.
    for (;;) {
        uint64_t begin = atomic_fetch_add_explicit(&level->index, 64, memory_order_relaxed);

        // Every request enters this level exactly once.
        if (begin >= level->count) {
            break;
        }
        uint64_t end = begin + 64 < level->count ? begin + 64 : level->count;

        // Neighbor lists are reconstructed from the one shared immutable field.
        for (uint64_t offset = begin; offset < end; ++offset) {
            uint32_t u = level->frontier[offset];
            uint32_t coset;
            uint32_t cell;
            kh_request(level->graph, u, &coset, &cell);

            // Right ownership is immutable until all BFS workers have joined.
            for (uint32_t k = 0; k < level->graph->field->parameters.f; ++k) {
                uint32_t v = kh_neighbor(level->graph, coset, cell, k);
                uint32_t next = level->matching->right[v];
                ++scans;

                // A free right endpoint ends the search after this entire level.
                if (next == UINT32_MAX) {
                    atomic_store_explicit(&level->found, true, memory_order_relaxed);
                    continue;
                }
                uint32_t unseen = UINT32_MAX;

                // Exactly one worker may claim a matched-left endpoint for the next level.
                if (__atomic_compare_exchange_n(&level->matching->distance[next], &unseen,
                    level->depth + 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                    uint64_t position = atomic_fetch_add_explicit(&level->tail, 1, memory_order_relaxed);
                    level->next[position] = next;
                }
            }
        }
    }
    atomic_fetch_add_explicit(&level->scans, scans, memory_order_relaxed);
    return NULL;
}

/*
 * Launch one worker batch with physical-core-first CPU placement.
 * Parameters: work_items: Unit count; threads: Requested workers; cpus: Selected CPUs; function,context: Worker callback and borrowed state; error: Diagnostic.
 * Returns: True after all workers join; false after joining any partially created workers.
 */
static bool launch_pinned(uint64_t work_items, uint32_t threads, const int *cpus,
                          void *(*function)(void *), void *context, const char **error) {
    pthread_t workers[CPU_SETSIZE];
    uint32_t active = threads;
    uint32_t created = 0;

    // Avoid worker-launch overhead when one chunk is enough.
    if (active > (work_items + 63) / 64) {
        active = (uint32_t)((work_items + 63) / 64);
    }

    // One worker executes within the inherited agent CPU allocation.
    if (active <= 1) {
        function(context);
        return true;
    }

    // Select distinct permitted CPUs, physical cores before sibling threads.
    for (uint32_t index = 0; index < active; ++index) {
        pthread_attr_t attributes;
        cpu_set_t affinity;
        CPU_ZERO(&affinity);
        CPU_SET(cpus[index], &affinity);

        // Attribute errors are operational rather than mathematical failures.
        if (pthread_attr_init(&attributes) != 0) {
            *error = "cannot initialize matching worker attributes";
            break;
        }
        int pinned = pthread_attr_setaffinity_np(&attributes, sizeof affinity, &affinity);
        int launched = pinned == 0 ? pthread_create(&workers[index], &attributes, function, context) : -1;
        pthread_attr_destroy(&attributes);

        // Join every worker already launched if a later launch fails.
        if (launched != 0) {
            *error = "cannot launch pinned matching worker";
            break;
        }
        ++created;
    }

    // Joining publishes all worker writes before a matching commit or next BFS level.
    for (uint32_t index = 0; index < created; ++index) {
        pthread_join(workers[index], NULL);
    }
    return created == active;
}

/*
 * Construct exact BFS levels in parallel while keeping DFS assignment serial.
 * Parameters: graph: Borrowed graph; matching: Mutable BFS scratch; threads: Worker count; cpus: Pinning; shortest: Output distance; error: Diagnostic.
 * Returns: True after complete BFS or no path; false on worker setup failure.
 */
static bool parallel_levels(const kh_graph_t *graph, kh_matching_t *matching, uint32_t threads,
                            const int *cpus, uint32_t *shortest, const char **error) {
    uint64_t count = 0;
    uint32_t depth = 0;
    uint32_t *frontier = matching->queue;
    uint32_t *next = matching->path;
    *shortest = UINT32_MAX;

    // Reset levels and DFS cursors before any worker observes the current assignment.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        matching->distance[u] = UINT32_MAX;
        matching->cursor[u] = 0;

        // Free left endpoints form the complete first frontier.
        if (matching->left[u] == UINT32_MAX) {
            matching->distance[u] = 0;
            frontier[count++] = u;
        }
    }

    // The joined worker phase provides a barrier between each alternating level.
    while (count != 0) {
        kh_bfs_level_t level = {0};
        level.graph = graph;
        level.matching = matching;
        level.frontier = frontier;
        level.next = next;
        level.count = count;
        level.depth = depth;
        atomic_init(&level.index, 0);
        atomic_init(&level.tail, 0);
        atomic_init(&level.scans, 0);
        atomic_init(&level.found, false);

        // Worker failures terminate the attempt without publishing a partial certificate.
        if (!launch_pinned(level.count, threads, cpus, scan_frontier, &level, error)) {
            return false;
        }
        matching->scans += atomic_load_explicit(&level.scans, memory_order_relaxed);

        // The first free right determines the shortest augmenting-path length.
        if (atomic_load_explicit(&level.found, memory_order_relaxed)) {
            *shortest = depth + 1;
            return true;
        }
        uint64_t following = atomic_load_explicit(&level.tail, memory_order_relaxed);

        // A corrupted queue would be an operational failure, not an obstruction.
        if (following > graph->count) {
            *error = "matching BFS frontier overflow";
            return false;
        }
        uint32_t *swap = frontier;
        frontier = next;
        next = swap;
        count = following;
        ++depth;
    }
    return true;
}

// Parallel proposals borrow the matching arrays without mutating any pair until joining.
typedef struct {
    const kh_graph_t *graph;
    kh_matching_t *matching;
    uint32_t *claimed_left;
    uint64_t *claimed_right;
    uint32_t shortest;
    _Atomic uint64_t next_root;
    _Atomic uint64_t scans;
    _Atomic uint64_t proposals;
} kh_proposal_phase_t;

/*
 * Search one snapshot for vertex-disjoint augmenting-path proposals.
 * Parameters: argument: Shared proposal phase with atomically claimed left/right vertices.
 * Returns: NULL after recording owned parents and candidate free rights.
 */
static void *propose_paths(void *argument) {
    kh_proposal_phase_t *phase = argument;
    const kh_graph_t *graph = phase->graph;
    kh_matching_t *matching = phase->matching;
    uint64_t scans = 0;
    uint64_t proposals = 0;

    // Each root belongs to one worker, but descendants may be reached by several roots.
    for (;;) {
        uint64_t begin = atomic_fetch_add_explicit(&phase->next_root, 64, memory_order_relaxed);

        // The entire free-root range has now been considered.
        if (begin >= graph->count) {
            break;
        }
        uint64_t end = begin + 64 < graph->count ? begin + 64 : graph->count;

        // Follow strictly increasing BFS layers from every available root.
        for (uint64_t index = begin; index < end; ++index) {
            uint32_t root = (uint32_t)index;

            // Matched requests cannot begin an augmenting path.
            if (matching->left[root] != UINT32_MAX) {
                continue;
            }
            uint32_t unseen = 0;

            // A root is claimed before its path-search scratch is changed.
            if (!__atomic_compare_exchange_n(&phase->claimed_left[root], &unseen, 1,
                false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                continue;
            }
            uint32_t u = root;
            bool found = false;

            // Parent links and the per-left cursor replace a thread-local full-size stack.
            for (;;) {
                uint32_t coset;
                uint32_t cell;
                bool descended = false;
                kh_request(graph, u, &coset, &cell);

                // Only the worker that claimed u may advance its edge cursor.
                while (matching->cursor[u] < graph->field->parameters.f) {
                    uint32_t k = matching->cursor[u]++;
                    uint32_t v = kh_neighbor(graph, coset, cell, k);
                    uint32_t next = matching->right[v];
                    ++scans;

                    // A unique free right endpoint completes this worker's proposal.
                    if (next == UINT32_MAX && matching->distance[u] + 1 == phase->shortest) {
                        uint64_t mask = UINT64_C(1) << (v % 64);
                        uint64_t previous = __atomic_fetch_or(&phase->claimed_right[v / 64], mask, __ATOMIC_RELAXED);

                        // A losing endpoint claim simply tries another neighbor.
                        if ((previous & mask) == 0) {
                            matching->path[root] = u;
                            matching->queue[root] = v;
                            found = true;
                            break;
                        }
                    }

                    // Every matched successor has a unique proposal owner.
                    if (next != UINT32_MAX && matching->distance[next] == matching->distance[u] + 1 &&
                        matching->distance[next] < phase->shortest) {
                        uint32_t unclaimed = 0;

                        // A claim conflict can lose a path but cannot create an invalid one.
                        if (__atomic_compare_exchange_n(&phase->claimed_left[next], &unclaimed, 1,
                            false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                            matching->path[next] = u;
                            u = next;
                            descended = true;
                            break;
                        }
                    }
                }

                // Successful proposals retain their parent links and selected cursors.
                if (found) {
                    ++proposals;
                    break;
                }

                // Backtrack only within vertices owned by this worker.
                if (descended) {
                    continue;
                }

                // Exhausted roots cannot make a proposal in this snapshot.
                if (u == root) {
                    break;
                }
                u = matching->path[u];
            }
        }
    }
    atomic_fetch_add_explicit(&phase->scans, scans, memory_order_relaxed);
    atomic_fetch_add_explicit(&phase->proposals, proposals, memory_order_relaxed);
    return NULL;
}

/*
 * Commit independently claimed paths only after all proposal workers stop.
 * Parameters: graph: Valid graph; matching: Mutable pairs and scratch; root: Proposed free root; endpoint: Claimed free right; shortest: Length bound.
 * Returns: True after one validated augmentation; false on an inconsistent proposal.
 */
static bool commit_path(const kh_graph_t *graph, kh_matching_t *matching, uint32_t root,
                        uint32_t endpoint, uint32_t shortest) {
    uint32_t u = matching->path[root];
    uint32_t v = endpoint;
    uint32_t steps = 0;

    // Parent links reverse a path without mutating another worker's proposal.
    for (;;) {
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);
        uint32_t k = matching->cursor[u] - 1;

        // Check every proposed edge before accepting it into the shared pairs.
        if (matching->cursor[u] == 0 || k >= graph->field->parameters.f ||
            kh_neighbor(graph, coset, cell, k) != v || ++steps > shortest) {
            return false;
        }
        uint32_t previous = matching->left[u];
        matching->left[u] = v;
        matching->right[v] = u;
        matching->choice[u] = k;

        // The root was free in the snapshot and ends this alternating chain.
        if (u == root) {
            if (previous != UINT32_MAX || steps != shortest) {
                return false;
            }
            ++matching->matched;
            return true;
        }

        // The old assigned right of this child is the parent's proposed endpoint.
        if (previous == UINT32_MAX) {
            return false;
        }
        v = previous;
        u = matching->path[u];
    }
}

/*
 * Produce disjoint proposals in parallel and commit only complete paths.
 * Parameters: graph: Borrowed graph; matching: Mutable state; shortest: BFS path length; threads: Worker count; cpus: Placement; committed: Output augmentations; error: Diagnostic.
 * Returns: True on a completed phase, including zero proposals; false without publishing an artifact.
 */
static bool parallel_augment(const kh_graph_t *graph, kh_matching_t *matching, uint32_t shortest,
                             uint32_t threads, const int *cpus, uint32_t *committed, const char **error) {
    uint64_t words = ((uint64_t)graph->field->parameters.q + 63) / 64;
    uint32_t *claimed_left = calloc(graph->count, sizeof(uint32_t));
    uint64_t *claimed_right = calloc((size_t)words, sizeof(uint64_t));

    // Failed scratch allocation is operational and cannot be reported as a Hall obstruction.
    if (claimed_left == NULL || claimed_right == NULL) {
        free(claimed_left);
        free(claimed_right);
        *error = "cannot allocate parallel augmentation claims";
        return false;
    }
    kh_proposal_phase_t phase = {0};
    phase.graph = graph;
    phase.matching = matching;
    phase.claimed_left = claimed_left;
    phase.claimed_right = claimed_right;
    phase.shortest = shortest;
    atomic_init(&phase.next_root, 0);
    atomic_init(&phase.scans, 0);
    atomic_init(&phase.proposals, 0);

    // Reset path scratch without altering the previous complete matching.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        matching->cursor[u] = 0;

        // A fresh root endpoint distinguishes a proposal from a failed search.
        if (matching->left[u] == UINT32_MAX) {
            matching->queue[u] = UINT32_MAX;
        }
    }
    bool valid = launch_pinned(graph->count, threads, cpus, propose_paths, &phase, error);
    matching->scans += atomic_load_explicit(&phase.scans, memory_order_relaxed);
    uint64_t proposals = atomic_load_explicit(&phase.proposals, memory_order_relaxed);
    free(claimed_left);
    free(claimed_right);

    // Incomplete worker launches cannot leave a published partial matching.
    if (!valid) {
        return false;
    }
    *committed = 0;

    // Disjoint left claims and free-right claims make each serial commit independent.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t root = (uint32_t)index;

        // Matched roots were not eligible when the snapshot was searched.
        if (matching->left[root] != UINT32_MAX || matching->queue[root] == UINT32_MAX) {
            continue;
        }

        // A malformed proposal is an operational error, not a valid result.
        if (!commit_path(graph, matching, root, matching->queue[root], shortest)) {
            *error = "invalid parallel augmentation proposal";
            return false;
        }
        ++*committed;
    }

    // Every accepted proposal must have produced exactly one committed path.
    if (*committed != proposals) {
        *error = "parallel proposal count mismatch";
        return false;
    }
    return true;
}

/*
 * Search the layered graph without recursive calls and commit one augmentation.
 * Parameters: graph: Borrowed graph; matching: Current state; root: Unmatched left; shortest: BFS terminal distance.
 * Returns: True after one augmentation; false if the root has no remaining layered path.
 */
static bool augment(const kh_graph_t *graph, kh_matching_t *matching, uint32_t root, uint32_t shortest) {
    uint64_t depth = 0;
    matching->path[0] = root;

    // The explicit stack has at most one entry per strictly increasing left level.
    for (;;) {
        uint32_t u = matching->path[depth];
        uint32_t coset;
        uint32_t cell;
        bool descended = false;
        kh_request(graph, u, &coset, &cell);

        // Per-vertex cursors ensure each candidate edge is scanned once in this DFS phase.
        while (matching->cursor[u] < graph->field->parameters.f) {
            uint32_t k = matching->cursor[u]++;
            uint32_t v = kh_neighbor(graph, coset, cell, k);
            uint32_t next = matching->right[v];
            ++matching->scans;

            // Commit the alternating path backwards from its free right endpoint.
            if (next == UINT32_MAX && matching->distance[u] + 1 == shortest) {
                uint32_t endpoint = v;

                // Previously matched edges supply each predecessor's new endpoint.
                for (uint64_t position = depth + 1; position > 0; --position) {
                    uint32_t left = matching->path[position - 1];
                    uint32_t previous = matching->left[left];
                    matching->left[left] = endpoint;
                    matching->right[endpoint] = left;
                    matching->choice[left] = matching->cursor[left] - 1;
                    endpoint = previous;
                }
                ++matching->matched;
                return true;
            }

            // An alternating matched edge must advance exactly one BFS layer.
            if (next != UINT32_MAX && matching->distance[next] == matching->distance[u] + 1 &&
                matching->distance[next] < shortest) {
                matching->path[++depth] = next;
                descended = true;
                break;
            }
        }

        // Continue at the child before exhausting or backtracking the parent.
        if (descended) {
            continue;
        }
        matching->distance[u] = UINT32_MAX;

        // Exhausting the root proves this phase cannot augment from it.
        if (depth == 0) {
            return false;
        }
        --depth;
    }
}

/*
 * Solve the implicit graph to exact maximum cardinality.
 * Parameters: graph: Valid graph; matching: Allocated empty state; threads: Pinned BFS worker count; error: Diagnostic.
 * Returns: True with exact maximum cardinality; false on CPU placement or worker failure.
 */
bool kh_matching_solve_with_hook(const kh_graph_t *graph, kh_matching_t *matching, uint32_t threads,
                                 kh_phase_callback_t callback, void *context, const char **error) {
    int cpus[CPU_SETSIZE];

    // Verify requested placement once, before mutating the matching.
    if (threads == 0 || !kh_select_cpus(threads, cpus, error)) {
        return false;
    }

    // The empty matching admits direct edges without needing a preceding BFS pass.
    if (threads > 1 && matching->phases == 0 && matching->matched == 0) {
        uint32_t committed = 0;

        // All free roots begin at distance zero, and every right endpoint is free.
        for (uint64_t index = 0; index < graph->count; ++index) {
            matching->distance[index] = 0;
        }

        // Atomic free-right claims produce a valid parallel greedy first phase.
        if (!parallel_augment(graph, matching, 1, threads, cpus, &committed, error)) {
            return false;
        }

        // Every valid nonempty field graph offers at least one first-phase edge.
        if (committed == 0) {
            *error = "parallel greedy phase made no progress";
            return false;
        }
        ++matching->phases;
        fprintf(stderr, "phase=%" PRIu64 " matched=%u/%u scans=%" PRIu64 " parallel=%u\n",
                matching->phases, matching->matched, graph->count, matching->scans, committed);

        // A callback sees only a committed, valid matching at a phase boundary.
        if (callback != NULL && !callback(graph, matching, context, error)) {
            return false;
        }
    }

    // Each remaining phase begins with exact BFS levels over the current assignment.
    while (matching->matched < graph->count) {
        uint32_t shortest;

        // The serial path remains a correctness reference and small-job option.
        if (threads == 1) {
            shortest = levels(graph, matching);
        } else if (!parallel_levels(graph, matching, threads, cpus, &shortest, error)) {
            return false;
        }

        // No free-right alternating path proves that the current matching is maximum.
        if (shortest == UINT32_MAX) {
            break;
        }
        uint32_t before = matching->matched;
        uint32_t committed = 0;

        // Workers propose disjoint paths against one immutable pair-array snapshot.
        if (threads > 1 && !parallel_augment(graph, matching, shortest, threads, cpus, &committed, error)) {
            return false;
        }

        // Claim conflicts may miss valid paths, so the exact serial search finishes stalled phases.
        if (threads == 1 || committed == 0) {

            // Proposal cursors may be exhausted; reset them before exact serial augmentation.
            if (threads > 1) {
                memset(matching->cursor, 0, (size_t)graph->count * sizeof(uint32_t));
            }

            // Every free root is considered in canonical order after the worker join.
            for (uint64_t index = 0; index < graph->count; ++index) {
                uint32_t u = (uint32_t)index;

                // Only free roots can start augmentations.
                if (matching->left[u] == UINT32_MAX) {
                    augment(graph, matching, u, shortest);
                }
            }
        }

        // A reported BFS path cannot finish a phase with unchanged cardinality.
        if (matching->matched == before) {
            *error = "matching phase found a path but made no progress";
            return false;
        }
        ++matching->phases;
        fprintf(stderr, "phase=%" PRIu64 " matched=%u/%u scans=%" PRIu64 " parallel=%u\n",
                matching->phases, matching->matched, graph->count, matching->scans, committed);

        // A callback sees only a committed, valid matching at a phase boundary.
        if (callback != NULL && !callback(graph, matching, context, error)) {
            return false;
        }
    }
    return true;
}

/*
 * Preserve the simple interface for callers that do not need phase checkpoints.
 * Parameters: graph: Valid graph; matching: Empty or restored state; threads: Worker count; error: Diagnostic.
 * Returns: True for a complete maximum matching; false on setup or search failure.
 */
bool kh_matching_solve(const kh_graph_t *graph, kh_matching_t *matching, uint32_t threads, const char **error) {
    return kh_matching_solve_with_hook(graph, matching, threads, NULL, NULL, error);
}

/*
 * Extract an exact Hall obstruction using alternating reachability.
 * Parameters: graph: Valid graph; matching: Maximum assignment; left_count,right_count: Output cardinalities.
 * Returns: True iff the reachable set certifies the claimed maximum's deficiency.
 */
bool kh_matching_hall(const kh_graph_t *graph, kh_matching_t *matching, uint32_t *left_count, uint32_t *right_count) {
    uint64_t head = 0;
    uint64_t tail = 0;
    uint64_t rights = 0;
    memset(matching->distance, 0, (size_t)graph->count * sizeof(uint32_t));
    memset(matching->right, 255, (size_t)graph->field->parameters.q * sizeof(uint32_t));

    // Reconstruct right ownership after reserving its high bit pattern for unseen endpoints.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;

        // Matched rights identify the next left vertex in alternating closure.
        if (matching->left[u] != UINT32_MAX) {
            matching->right[matching->left[u]] = u;
        } else {
            matching->distance[u] = 1;
            matching->queue[tail++] = u;
        }
    }

    // Cursor scratch becomes a right-visited bitmap borrowed from a temporary allocation.
    size_t bytes = ((uint64_t)graph->field->parameters.q + 7) / 8;
    unsigned char *seen = calloc(bytes, 1);

    // Allocation failure cannot be treated as a mathematical obstruction.
    if (seen == NULL) {
        return false;
    }

    // Enumerating all neighbors computes N(S), not just the matching's selected edges.
    while (head < tail) {
        uint32_t u = matching->queue[head++];
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);

        // Close under right endpoints and their matched left partners.
        for (uint32_t k = 0; k < graph->field->parameters.f; ++k) {
            uint32_t v = kh_neighbor(graph, coset, cell, k);
            unsigned char bit = (unsigned char)(1u << (v % 8));

            // Shared neighbors count only once in the Hall neighborhood.
            if ((seen[v / 8] & bit) != 0) {
                continue;
            }
            seen[v / 8] |= bit;
            ++rights;
            uint32_t next = matching->right[v];

            // A reachable free right would contradict the completed maximum search.
            if (next == UINT32_MAX) {
                free(seen);
                return false;
            }

            // Every left vertex enters the closure queue at most once.
            if (matching->distance[next] == 0) {
                matching->distance[next] = 1;
                matching->queue[tail++] = next;
            }
        }
    }
    free(seen);
    *left_count = (uint32_t)tail;
    *right_count = (uint32_t)rights;
    return tail > rights && tail - rights == (uint64_t)graph->count - matching->matched;
}
