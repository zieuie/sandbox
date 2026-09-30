#define _GNU_SOURCE
#include "kh_shard.h"

#include "kh_field.h"
#include "kh_matching.h"
#include "kh_solver.h"
#include "kh_threads.h"

#include <stddef.h>
#include <sched.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    struct kh_shard_graph *graph;
    pthread_t thread;
    uint32_t index;
    int cpu;
} parallel_worker_t;

struct kh_shard_graph {
    kh_field_t field;
    kh_request_block_t *blocks;
    uint32_t block_count;
    uint32_t count;
    uint32_t threads;
    parallel_worker_t *workers;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    bool mutex_ready;
    bool condition_ready;
    bool stopping;
    bool affinity_failed;
    uint32_t created;
    uint32_t ready;
    uint32_t completed;
    uint64_t generation;
    uint64_t parallel_count;
    _Atomic uint64_t parallel_next;
    uint64_t parallel_chunk;
    kh_shard_parallel_function_t parallel_function;
    void *parallel_context;
};

static void *parallel_main(void *raw) {
    parallel_worker_t *worker = raw;
    kh_shard_graph_t *graph = worker->graph;
    cpu_set_t affinity;
    CPU_ZERO(&affinity);
    CPU_SET(worker->cpu, &affinity);
    bool affinity_failed = pthread_setaffinity_np(
        pthread_self(), sizeof affinity, &affinity) != 0;
    pthread_mutex_lock(&graph->mutex);
    graph->affinity_failed = graph->affinity_failed || affinity_failed;
    ++graph->ready;
    pthread_cond_broadcast(&graph->condition);
    uint64_t generation = 0;
    while (!graph->stopping) {
        while (!graph->stopping && graph->generation == generation) {
            pthread_cond_wait(&graph->condition, &graph->mutex);
        }
        if (graph->stopping) {
            break;
        }
        generation = graph->generation;
        uint64_t count = graph->parallel_count;
        kh_shard_parallel_function_t function = graph->parallel_function;
        void *context = graph->parallel_context;
        pthread_mutex_unlock(&graph->mutex);
        // Paths and active frontiers are uneven. Pull bounded chunks instead
        // of stranding workers behind one expensive fixed partition.
        for (;;) {
            uint64_t begin = atomic_fetch_add_explicit(
                &graph->parallel_next, graph->parallel_chunk, memory_order_relaxed);
            if (begin >= count) break;
            uint64_t end = count - begin < graph->parallel_chunk
                             ? count : begin + graph->parallel_chunk;
            function(context, begin, end, worker->index);
        }
        pthread_mutex_lock(&graph->mutex);
        ++graph->completed;
        pthread_cond_broadcast(&graph->condition);
    }
    pthread_mutex_unlock(&graph->mutex);
    return NULL;
}

static bool parallel_create(kh_shard_graph_t *graph, const char **error) {
    graph->workers = calloc(graph->threads, sizeof *graph->workers);
    int *cpus = calloc(graph->threads, sizeof *cpus);
    if (graph->workers == NULL || cpus == NULL ||
        !kh_select_cpus(graph->threads, cpus, error) ||
        pthread_mutex_init(&graph->mutex, NULL) != 0) {
        free(cpus);
        *error = "cannot allocate pinned matching workers";
        return false;
    }
    graph->mutex_ready = true;
    if (pthread_cond_init(&graph->condition, NULL) != 0) {
        free(cpus);
        *error = "cannot initialize matching worker condition";
        return false;
    }
    graph->condition_ready = true;
    for (uint32_t index = 0; index < graph->threads; ++index) {
        parallel_worker_t *worker = &graph->workers[index];
        worker->graph = graph;
        worker->index = index;
        worker->cpu = cpus[index];
        if (pthread_create(&worker->thread, NULL, parallel_main, worker) != 0) {
            free(cpus);
            *error = "cannot create pinned matching worker";
            return false;
        }
        ++graph->created;
    }
    free(cpus);
    pthread_mutex_lock(&graph->mutex);
    while (graph->ready < graph->threads) {
        pthread_cond_wait(&graph->condition, &graph->mutex);
    }
    bool valid = !graph->affinity_failed;
    pthread_mutex_unlock(&graph->mutex);
    if (!valid) {
        *error = "cannot pin matching worker to its CPU";
    }
    return valid;
}

static void parallel_destroy(kh_shard_graph_t *graph) {
    if (graph->mutex_ready) {
        pthread_mutex_lock(&graph->mutex);
        graph->stopping = true;
        if (graph->condition_ready) {
            pthread_cond_broadcast(&graph->condition);
        }
        pthread_mutex_unlock(&graph->mutex);
    }
    for (uint32_t index = 0; index < graph->created; ++index) {
        pthread_join(graph->workers[index].thread, NULL);
    }
    if (graph->condition_ready) {
        pthread_cond_destroy(&graph->condition);
    }
    if (graph->mutex_ready) {
        pthread_mutex_destroy(&graph->mutex);
    }
    free(graph->workers);
    graph->workers = NULL;
}

void kh_shard_parallel(const kh_shard_graph_t *constant_graph, uint64_t count,
                       kh_shard_parallel_function_t function, void *context) {
    kh_shard_graph_t *graph = (kh_shard_graph_t *)(uintptr_t)constant_graph;
    pthread_mutex_lock(&graph->mutex);
    graph->parallel_count = count;
    atomic_store_explicit(&graph->parallel_next, 0, memory_order_relaxed);
    // At least eight opportunities per worker, with bounded scheduling cost.
    graph->parallel_chunk = count / ((uint64_t)graph->threads * 8);
    if (graph->parallel_chunk < 1) graph->parallel_chunk = 1;
    if (graph->parallel_chunk > 256) graph->parallel_chunk = 256;
    graph->parallel_function = function;
    graph->parallel_context = context;
    graph->completed = 0;
    ++graph->generation;
    pthread_cond_broadcast(&graph->condition);
    while (graph->completed < graph->threads) {
        pthread_cond_wait(&graph->condition, &graph->mutex);
    }
    pthread_mutex_unlock(&graph->mutex);
}

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
    if (!parallel_create(graph, error)) {
        kh_shard_graph_free(graph);
        return NULL;
    }
    return graph;
}

void kh_shard_graph_free(kh_shard_graph_t *graph) {
    if (graph == NULL) {
        return;
    }
    parallel_destroy(graph);
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

uint32_t kh_shard_graph_threads(const kh_shard_graph_t *graph) {
    return graph == NULL ? 0 : graph->threads;
}

typedef struct {
    const kh_shard_graph_t *graph;
    const uint32_t *left;
    uint32_t *labels;
} scan_context_t;

static void scan_range(void *raw, uint64_t begin, uint64_t end,
                       uint32_t worker_index) {
    (void)worker_index;
    scan_context_t *context = raw;
    uint32_t f = context->graph->field.parameters.f;
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
    scan_context_t context = {graph, left, labels};
    kh_shard_parallel(graph, left_count, scan_range, &context);
    return true;
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
