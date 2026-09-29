#ifndef KH_SHARD_H
#define KH_SHARD_H

#include <stdbool.h>
#include <stdint.h>

// Opaque native field plus compact request descriptors for one shard process.
typedef struct kh_shard_graph kh_shard_graph_t;

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
