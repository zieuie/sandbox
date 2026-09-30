#ifndef KH_SHARD_H
#define KH_SHARD_H

#include <stdbool.h>
#include <stdint.h>

// Opaque native field plus compact request descriptors for one shard process.
typedef struct kh_shard_graph kh_shard_graph_t;

/* Bound one shard's path reply independently of the full graph size. */
#define KH_SHARD_PROPOSAL_LIMIT UINT32_C(262144)

/* Build one compact primitive-X field and canonical request-block index. */
kh_shard_graph_t *kh_shard_graph_create(
    uint32_t p,
    uint32_t r,
    uint32_t threads,
    const uint16_t *polynomial,
    uint32_t block_count,
    const uint16_t *stripes,
    const uint32_t *copies,
    uint64_t max_bytes,
    const char **error
);

/* Release an opaque graph returned by kh_shard_graph_create. */
void kh_shard_graph_free(kh_shard_graph_t *graph);

uint32_t kh_shard_graph_count(const kh_shard_graph_t *graph);
uint32_t kh_shard_graph_q(const kh_shard_graph_t *graph);
uint32_t kh_shard_graph_f(const kh_shard_graph_t *graph);
uint32_t kh_shard_graph_threads(const kh_shard_graph_t *graph);

/* Run disjoint chunks on persistent pinned workers; a worker may call back repeatedly. */
typedef void (*kh_shard_parallel_function_t)(
    void *context, uint64_t begin, uint64_t end, uint32_t worker_index);
void kh_shard_parallel(
    const kh_shard_graph_t *graph,
    uint64_t count,
    kh_shard_parallel_function_t function,
    void *context
);

/* Generate all F neighbors for each canonical left request in one batch. */
bool kh_shard_scan(
    const kh_shard_graph_t *graph,
    const uint32_t *left,
    uint64_t left_count,
    uint32_t *labels,
    const char **error
);

/* Validate or reconstruct one selected implicit edge. */
bool kh_shard_neighbor(
    const kh_shard_graph_t *graph,
    uint32_t left,
    uint32_t choice,
    uint32_t *right
);

#endif
