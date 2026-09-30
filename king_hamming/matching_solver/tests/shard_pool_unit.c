#define _GNU_SOURCE
#include "kh_shard.h"

#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    _Atomic unsigned visits[1009];
    unsigned calls[4];
    int cpus[4];
    bool rendezvous;
    pthread_barrier_t barrier;
} context_t;

static void visit(void *raw, uint64_t begin, uint64_t end, uint32_t worker) {
    context_t *context = raw;
    assert(worker < 4 && begin < end && end <= 1009);
    cpu_set_t affinity;
    assert(sched_getaffinity(0, sizeof affinity, &affinity) == 0);
    assert(CPU_COUNT(&affinity) == 1);
    int cpu = sched_getcpu();
    assert(CPU_ISSET(cpu, &affinity));
    if (context->calls[worker]++ == 0) {
        context->cpus[worker] = cpu;
        // Force the first chunk onto every worker, independently of OS timing.
        if (context->rendezvous) pthread_barrier_wait(&context->barrier);
    }
    assert(context->cpus[worker] == cpu);
    for (uint64_t index = begin; index < end; ++index) {
        assert(atomic_fetch_add(&context->visits[index], 1) == 0);
    }
}

int main(void) {
    cpu_set_t affinity;
    assert(sched_getaffinity(0, sizeof affinity, &affinity) == 0);
    unsigned threads = CPU_COUNT(&affinity) < 4 ? (unsigned)CPU_COUNT(&affinity) : 4;
    const uint16_t polynomial[] = {2, 3, 0, 1}, stripes[] = {1};
    const uint32_t copies[] = {1};
    const char *error = NULL;
    kh_shard_graph_t *graph = kh_shard_graph_create(
        5, 3, threads, polynomial, 1, stripes, copies, UINT64_C(2147483648), &error);
    assert(graph != NULL);
    context_t context = {0};
    context.rendezvous = true;
    assert(pthread_barrier_init(&context.barrier, NULL, threads) == 0);
    kh_shard_parallel(graph, 1009, visit, &context);
    unsigned calls = 0;
    for (unsigned worker = 0; worker < threads; ++worker) {
        assert(context.calls[worker] > 0);
        calls += context.calls[worker];
        for (unsigned other = 0; other < worker; ++other) {
            assert(context.cpus[worker] != context.cpus[other]);
        }
    }
    assert(calls > threads);
    for (unsigned index = 0; index < 1009; ++index) assert(context.visits[index] == 1);
    pthread_barrier_destroy(&context.barrier);
    memset(&context, 0, sizeof context);
    kh_shard_parallel(graph, 0, visit, &context);
    kh_shard_parallel(graph, 1, visit, &context);
    assert(context.visits[0] == 1);
    kh_shard_graph_free(graph);
    puts("dynamic shard chunks cover work exactly once on distinct pinned CPUs");
    return 0;
}
