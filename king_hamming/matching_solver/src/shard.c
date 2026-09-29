#include "kh_shard.h"

#include "kh_field.h"
#include "kh_matching.h"
#include "kh_solver.h"

#include <stddef.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

struct kh_shard_graph {
    kh_field_t field;
    kh_request_block_t *blocks;
    uint32_t block_count;
    uint32_t count;
    uint32_t threads;
};

// Decode a request without exposing the full production matching allocation.
static void request(const kh_shard_graph_t *graph, uint32_t left,
                    uint32_t *coset, uint32_t *cell) {
    uint32_t low = 0;
    uint32_t high = graph->block_count;
    while (low + 1 < high) {
        uint32_t middle = low + (high - low) / 2;
        if (graph->blocks[middle].first <= left) {
            low = middle;
        } else {
            high = middle;
        }
    }
    const kh_request_block_t *block = &graph->blocks[low];
    uint64_t offset = left - block->first;
    uint32_t width = (uint32_t)block->stripes * graph->field.parameters.f;
    *coset = block->coset + (uint32_t)(offset / width);
    *cell = (uint32_t)(offset % width);
}

// Shift one compact field label by the request's inverse coset power.
static uint32_t neighbor(const kh_shard_graph_t *graph, uint32_t coset,
                         uint32_t cell, uint32_t choice) {
    uint32_t f = graph->field.parameters.f;
    uint32_t q = graph->field.parameters.q;
    uint32_t label = graph->field.cells[(uint64_t)cell * f + choice];
    return label == 0 ? 0 : 1 + (uint32_t)(((uint64_t)label - 1 + q - 1 - coset) % (q - 1));
}

kh_shard_graph_t *kh_shard_graph_create(
    uint32_t p, uint32_t r, uint32_t threads, const uint16_t *polynomial,
    uint32_t block_count, const uint16_t *stripes, const uint32_t *copies,
    uint64_t max_bytes, const char **error) {
    kh_parameters_t parameters;
    if (polynomial == NULL || stripes == NULL || copies == NULL || block_count == 0 ||
        threads == 0 || threads > 1024 ||
        !kh_parameters(p, r, &parameters, error) ||
        block_count > parameters.budget ||
        (uint64_t)block_count * sizeof(kh_request_block_t) > max_bytes) {
        *error = "invalid native shard graph parameters";
        return NULL;
    }
    kh_shard_graph_t *graph = calloc(1, sizeof *graph);
    if (graph == NULL) {
        *error = "cannot allocate native shard graph";
        return NULL;
    }
    graph->blocks = calloc(block_count, sizeof *graph->blocks);
    if (graph->blocks == NULL) {
        *error = "cannot allocate native shard request blocks";
        free(graph);
        return NULL;
    }
    uint64_t request_count = 0;
    uint64_t coset = 0;
    bool valid = true;
    for (uint32_t index = 0; index < block_count; ++index) {
        uint64_t block_stripes = stripes[index];
        uint64_t block_copies = copies[index];
        if (block_stripes == 0 || block_stripes > parameters.p || block_copies == 0 ||
            block_copies > parameters.budget / block_stripes ||
            request_count + block_stripes * block_copies * parameters.f > UINT32_MAX ||
            coset + block_copies + 1 > parameters.q - 1) {
            valid = false;
            break;
        }
        graph->blocks[index].first = request_count;
        graph->blocks[index].coset = (uint32_t)coset;
        graph->blocks[index].copies = copies[index];
        graph->blocks[index].stripes = stripes[index];
        request_count += block_stripes * block_copies * parameters.f;
        coset += block_copies;
    }
    if (!valid || request_count == 0) {
        *error = "invalid native shard request blocks";
        kh_shard_graph_free(graph);
        return NULL;
    }
    graph->block_count = block_count;
    graph->count = (uint32_t)request_count;
    graph->threads = threads;
    uint64_t descriptors = (uint64_t)block_count * sizeof(kh_request_block_t);
    if (!kh_primitive(&parameters, polynomial) ||
        !kh_build_field(&parameters, polynomial, threads, max_bytes - descriptors,
                        &graph->field, error)) {
        if (*error == NULL) {
            *error = "polynomial is not primitive with generator X";
        }
        kh_shard_graph_free(graph);
        return NULL;
    }
    return graph;
}

void kh_shard_graph_free(kh_shard_graph_t *graph) {
    if (graph == NULL) {
        return;
    }
    kh_free_field(&graph->field);
    free(graph->blocks);
    free(graph);
}

uint32_t kh_shard_graph_count(const kh_shard_graph_t *graph) {
    return graph == NULL ? 0 : graph->count;
}

uint32_t kh_shard_graph_q(const kh_shard_graph_t *graph) {
    return graph == NULL ? 0 : graph->field.parameters.q;
}

uint32_t kh_shard_graph_f(const kh_shard_graph_t *graph) {
    return graph == NULL ? 0 : graph->field.parameters.f;
}

typedef struct {
    const kh_shard_graph_t *graph;
    const uint32_t *left;
    uint64_t left_count;
    uint32_t *labels;
    _Atomic uint64_t next;
} scan_context_t;

static void *scan_batch(void *raw) {
    scan_context_t *context = raw;
    uint32_t f = context->graph->field.parameters.f;
    for (;;) {
        uint64_t begin = atomic_fetch_add_explicit(&context->next, 64, memory_order_relaxed);
        if (begin >= context->left_count) {
            break;
        }
        uint64_t end = begin + 64 < context->left_count ? begin + 64 : context->left_count;
        for (uint64_t index = begin; index < end; ++index) {
            uint32_t coset;
            uint32_t cell;
            request(context->graph, context->left[index], &coset, &cell);
            for (uint32_t choice = 0; choice < f; ++choice) {
                context->labels[index * f + choice] =
                    neighbor(context->graph, coset, cell, choice);
            }
        }
    }
    return NULL;
}

bool kh_shard_scan(const kh_shard_graph_t *graph, const uint32_t *left,
                   uint64_t left_count, uint32_t *labels, const char **error) {
    if (graph == NULL || (left_count != 0 && (left == NULL || labels == NULL)) ||
        left_count > SIZE_MAX / graph->field.parameters.f) {
        *error = "invalid native shard scan buffers";
        return false;
    }
    for (uint64_t index = 0; index < left_count; ++index) {
        if (left[index] >= graph->count) {
            *error = "native shard left request is outside graph";
            return false;
        }
    }
    scan_context_t context = {graph, left, left_count, labels, 0};
    uint32_t active = graph->threads;
    uint64_t chunks = (left_count + 63) / 64;
    if (active > chunks) {
        active = (uint32_t)chunks;
    }
    if (active <= 1) {
        scan_batch(&context);
        return true;
    }
    pthread_t *workers = calloc(active, sizeof *workers);
    if (workers == NULL) {
        *error = "cannot allocate native shard scan workers";
        return false;
    }
    uint32_t created = 0;
    for (; created < active; ++created) {
        if (pthread_create(&workers[created], NULL, scan_batch, &context) != 0) {
            *error = "cannot launch native shard scan worker";
            break;
        }
    }
    for (uint32_t index = 0; index < created; ++index) {
        pthread_join(workers[index], NULL);
    }
    free(workers);
    return created == active;
}

bool kh_shard_neighbor(const kh_shard_graph_t *graph, uint32_t left,
                       uint32_t choice, uint32_t *right) {
    if (graph == NULL || right == NULL || left >= graph->count ||
        choice >= graph->field.parameters.f) {
        return false;
    }
    uint32_t coset;
    uint32_t cell;
    request(graph, left, &coset, &cell);
    *right = neighbor(graph, coset, cell, choice);
    return true;
}
