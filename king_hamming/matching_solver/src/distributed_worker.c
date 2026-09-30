#define _GNU_SOURCE

#include "kh_shard.h"
#include "kh_resource.h"
#include "kh_wire.h"

#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define ABSENT UINT32_MAX
#define PATH_START UINT32_C(0x80000000)

typedef struct {
    uint32_t p;
    uint32_t r;
    uint32_t threads;
    uint32_t block_count;
    uint64_t max_bytes;
    uint32_t polynomial_count;
    uint16_t *polynomial;
    uint16_t *stripes;
    uint32_t *copies;
} init_t;

typedef struct {
    uint32_t left;
    uint32_t right;
    uint32_t choice;
} assignment_t;

typedef struct {
    kh_shard_graph_t *graph;
    uint32_t index;
    uint32_t workers;
    uint32_t n;
    uint32_t q;
    uint32_t f;
    uint32_t *left;
    uint32_t *right;
    uint16_t *choice;
    uint32_t *distance;
    unsigned char *seen_left;
    unsigned char *right_bits;
} shard_t;

static void init_free(init_t *init) {
    free(init->polynomial);
    free(init->stripes);
    free(init->copies);
    memset(init, 0, sizeof *init);
}

static void shard_free(shard_t *shard) {
    kh_shard_graph_free(shard->graph);
    free(shard->left);
    free(shard->right);
    free(shard->choice);
    free(shard->distance);
    free(shard->seen_left);
    free(shard->right_bits);
    memset(shard, 0, sizeof *shard);
}

static bool bit_test(const unsigned char *bits, uint32_t index) {
    return ((bits[index >> 3] >> (index & 7)) & 1U) != 0;
}

static void bit_set(unsigned char *bits, uint32_t index) {
    bits[index >> 3] |= (unsigned char)(1U << (index & 7));
}

static bool bit_test_set_atomic(unsigned char *bits, uint32_t index) {
    unsigned char mask = (unsigned char)(1U << (index & 7));
    return (__atomic_fetch_or(&bits[index >> 3], mask, __ATOMIC_RELAXED) & mask) != 0;
}

static bool read_init(int descriptor, uint64_t payload_bytes, init_t *init) {
    const uint64_t fixed = 4 * UINT64_C(5) + 8;
    if (payload_bytes < fixed ||
        !kh_wire_read_u32(descriptor, &init->p) ||
        !kh_wire_read_u32(descriptor, &init->r) ||
        !kh_wire_read_u32(descriptor, &init->threads) ||
        !kh_wire_read_u32(descriptor, &init->block_count) ||
        !kh_wire_read_u64(descriptor, &init->max_bytes) ||
        !kh_wire_read_u32(descriptor, &init->polynomial_count)) {
        return false;
    }
    uint64_t expected = fixed + 4 * (uint64_t)init->polynomial_count +
                        8 * (uint64_t)init->block_count;
    if (expected != payload_bytes || init->polynomial_count != init->r + 1 ||
        init->polynomial_count > 32 || init->block_count == 0 ||
        init->block_count > UINT32_C(100000000)) {
        return false;
    }
    init->polynomial = calloc(init->polynomial_count, sizeof *init->polynomial);
    init->stripes = calloc(init->block_count, sizeof *init->stripes);
    init->copies = calloc(init->block_count, sizeof *init->copies);
    if (init->polynomial == NULL || init->stripes == NULL || init->copies == NULL) {
        return false;
    }
    for (uint32_t index = 0; index < init->polynomial_count; ++index) {
        uint32_t value;
        if (!kh_wire_read_u32(descriptor, &value) || value > UINT16_MAX) {
            return false;
        }
        init->polynomial[index] = (uint16_t)value;
    }
    for (uint32_t index = 0; index < init->block_count; ++index) {
        uint32_t stripes;
        if (!kh_wire_read_u32(descriptor, &stripes) || stripes > UINT16_MAX ||
            !kh_wire_read_u32(descriptor, &init->copies[index])) {
            return false;
        }
        init->stripes[index] = (uint16_t)stripes;
    }
    return true;
}

static bool apply_affinity(const char *text) {
    if (text == NULL) {
        return true;
    }
    char *copy = strdup(text);
    if (copy == NULL) {
        return false;
    }
    cpu_set_t set;
    CPU_ZERO(&set);
    bool any = false;
    char *save = NULL;
    for (char *item = strtok_r(copy, ",", &save); item != NULL;
         item = strtok_r(NULL, ",", &save)) {
        char *end = NULL;
        errno = 0;
        unsigned long value = strtoul(item, &end, 10);
        if (errno != 0 || *item == '\0' || *end != '\0' || value >= CPU_SETSIZE) {
            free(copy);
            return false;
        }
        CPU_SET((int)value, &set);
        any = true;
    }
    free(copy);
    return any && sched_setaffinity(0, sizeof set, &set) == 0;
}

static bool shard_create(shard_t *shard, uint32_t index, uint32_t workers,
                         const init_t *init, const char **error) {
    shard->graph = kh_shard_graph_create(
        init->p, init->r, init->threads, init->polynomial, init->block_count,
        init->stripes, init->copies, init->max_bytes, error);
    if (shard->graph == NULL) {
        return false;
    }
    shard->index = index;
    shard->workers = workers;
    shard->n = kh_shard_graph_count(shard->graph);
    shard->q = kh_shard_graph_q(shard->graph);
    shard->f = kh_shard_graph_f(shard->graph);
    if (shard->f > UINT16_MAX) {
        *error = "native shard choice width exceeds compact representation";
        return false;
    }
    uint64_t n = shard->n;
    uint64_t q = shard->q;
    uint64_t bit_n = (n + 7) / 8;
    uint64_t bit_q = (q + 7) / 8;
    uint64_t proposal_limit = n < KH_SHARD_PROPOSAL_LIMIT
                              ? n : KH_SHARD_PROPOSAL_LIMIT;
    uint64_t persistent = 10 * n + bit_n + 8 * q + bit_q;
    uint64_t level_scratch = 4 * n + 4 * ((n + shard->workers - 1) / shard->workers) +
        (uint64_t)init->threads * (UINT64_C(65536) * 4 + 16);
    uint64_t proposal_scratch = bit_n + bit_q +
        proposal_limit * sizeof(assignment_t) +
        (uint64_t)init->threads * 16 * (proposal_limit + 1);
    uint64_t apply_scratch = (uint64_t)shard->workers * proposal_limit *
                             sizeof(assignment_t);
    uint64_t scratch = level_scratch > proposal_scratch
                       ? level_scratch : proposal_scratch;
    if (apply_scratch > scratch) {
        scratch = apply_scratch;
    }
    uint64_t state_required = persistent + scratch + UINT64_C(67108864);
    if (state_required > init->max_bytes) {
        *error = "native shard replicated state exceeds memory limit";
        return false;
    }
    shard->left = malloc((size_t)shard->n * sizeof(uint32_t));
    shard->right = malloc((size_t)shard->q * sizeof(uint32_t));
    shard->choice = malloc((size_t)shard->n * sizeof(uint16_t));
    shard->distance = malloc((size_t)shard->n * sizeof(uint32_t));
    shard->seen_left = malloc(((size_t)shard->n + 7) / 8);
    shard->right_bits = calloc(((uint64_t)shard->q + 7) / 8, 1);
    if (shard->left == NULL || shard->right == NULL || shard->choice == NULL ||
        shard->distance == NULL || shard->seen_left == NULL || shard->right_bits == NULL) {
        *error = "cannot allocate native shard matching state";
        return false;
    }
    memset(shard->left, 255, (size_t)shard->n * sizeof(uint32_t));
    memset(shard->right, 255, (size_t)shard->q * sizeof(uint32_t));
    memset(shard->choice, 255, (size_t)shard->n * sizeof(uint16_t));
    memset(shard->distance, 255, (size_t)shard->n * sizeof(uint32_t));
    return true;
}

static bool handle_scan(shard_t *shard, uint64_t count, const char **error) {
    if (count > UINT32_MAX || count > SIZE_MAX / sizeof(uint32_t) ||
        count > SIZE_MAX / (sizeof(uint32_t) * (uint64_t)shard->f)) {
        return false;
    }
    uint32_t *left = malloc((size_t)count * sizeof *left);
    uint32_t *labels = malloc((size_t)count * shard->f * sizeof *labels);
    bool valid = count == 0 || (left != NULL && labels != NULL);
    valid = valid && kh_wire_read_u32s(STDIN_FILENO, left, (size_t)count);
    for (uint64_t position = 0; valid && position < count; ++position) {
        valid = left[position] < shard->n &&
                left[position] % shard->workers == shard->index;
    }
    valid = valid && kh_shard_scan(shard->graph, left, count, labels, error);
    uint64_t label_count = count * shard->f;
    valid = valid && kh_wire_write_response(STDOUT_FILENO, 0, label_count);
    valid = valid && label_count <= SIZE_MAX &&
            kh_wire_write_u32s(STDOUT_FILENO, labels, (size_t)label_count);
    free(left);
    free(labels);
    return valid;
}

#define DISCOVERY_CHUNK_ITEMS UINT32_C(65536)

typedef struct discovery_chunk {
    struct discovery_chunk *next;
    uint32_t count;
    uint32_t items[DISCOVERY_CHUNK_ITEMS];
} discovery_chunk_t;

typedef struct {
    discovery_chunk_t *first;
    discovery_chunk_t *last;
    uint64_t count;
} discovery_vector_t;

static bool discovery_append(discovery_vector_t *vector, uint32_t item) {
    if (vector->last == NULL || vector->last->count == DISCOVERY_CHUNK_ITEMS) {
        discovery_chunk_t *chunk = malloc(sizeof *chunk);
        if (chunk == NULL) {
            return false;
        }
        chunk->next = NULL;
        chunk->count = 0;
        if (vector->last == NULL) {
            vector->first = chunk;
        } else {
            vector->last->next = chunk;
        }
        vector->last = chunk;
    }
    vector->last->items[vector->last->count++] = item;
    ++vector->count;
    return true;
}

static void discovery_free(discovery_vector_t *vector) {
    discovery_chunk_t *chunk = vector->first;
    while (chunk != NULL) {
        discovery_chunk_t *next = chunk->next;
        free(chunk);
        chunk = next;
    }
}

typedef struct {
    shard_t *shard;
    const uint32_t *owned;
    discovery_vector_t *discovered;
    _Atomic bool free_right;
    _Atomic bool valid;
} level_context_t;

static void level_range(void *raw, uint64_t begin, uint64_t end,
                        uint32_t worker_index) {
    (void)worker_index;
    level_context_t *context = raw;
    shard_t *shard = context->shard;
    for (uint64_t item = begin; item < end &&
         atomic_load_explicit(&context->valid, memory_order_relaxed); ++item) {
        uint32_t left = context->owned[item];
        for (uint32_t choice = 0; choice < shard->f; ++choice) {
            uint32_t right;
            if (!kh_shard_neighbor(shard->graph, left, choice, &right) || right >= shard->q) {
                atomic_store_explicit(&context->valid, false, memory_order_relaxed);
                break;
            }
            __atomic_fetch_or(&shard->right_bits[right >> 3],
                              (unsigned char)(1U << (right & 7)), __ATOMIC_RELAXED);
            uint32_t mate = shard->right[right];
            if (mate == ABSENT) {
                atomic_store_explicit(&context->free_right, true, memory_order_relaxed);
            } else if (shard->distance[mate] == ABSENT &&
                       !bit_test_set_atomic(shard->seen_left, mate)) {
                discovery_vector_t *vector = &context->discovered[worker_index];
                if (!discovery_append(vector, mate)) {
                    atomic_store_explicit(&context->valid, false, memory_order_relaxed);
                    break;
                }
            }
        }
    }
}

static bool handle_level(shard_t *shard, uint64_t count, const char **error) {
    uint32_t depth;
    if (count > shard->n || !kh_wire_read_u32(STDIN_FILENO, &depth) || depth >= shard->n) {
        return false;
    }
    if (depth == 0) {
        memset(shard->distance, 255, (size_t)shard->n * sizeof(uint32_t));
        memset(shard->right_bits, 0, ((uint64_t)shard->q + 7) / 8);
    }
    uint32_t owned_count = 0;
    uint32_t batch[4096];
    for (uint64_t offset = 0; offset < count;) {
        size_t chunk = count - offset < 4096 ? (size_t)(count - offset) : 4096;
        if (!kh_wire_read_u32s(STDIN_FILENO, batch, chunk)) {
            return false;
        }
        for (size_t index = 0; index < chunk; ++index) {
            uint32_t left = batch[index];
            if (left >= shard->n) {
                return false;
            }
            shard->distance[left] = depth;
            owned_count += left % shard->workers == shard->index;
        }
        offset += chunk;
    }
    uint32_t *owned = malloc((size_t)owned_count * sizeof(uint32_t));
    uint32_t thread_count = kh_shard_graph_threads(shard->graph);
    discovery_vector_t *discovered = calloc(thread_count, sizeof *discovered);
    if ((owned_count != 0 && owned == NULL) || discovered == NULL) {
        free(owned);
        free(discovered);
        return false;
    }
    uint32_t filled = 0;
    for (uint32_t left = shard->index; left < shard->n; left += shard->workers) {
        if (shard->distance[left] == depth) {
            owned[filled++] = left;
        }
    }
    if (filled != owned_count) {
        free(owned);
        free(discovered);
        return false;
    }
    memset(shard->seen_left, 0, ((size_t)shard->n + 7) / 8);
    level_context_t context = {shard, owned, discovered, false, true};
    kh_shard_parallel(shard->graph, owned_count, level_range, &context);
    uint64_t discovered_count = 0;
    for (uint32_t index = 0; index < thread_count; ++index) {
        discovered_count += discovered[index].count;
    }
    bool valid = atomic_load_explicit(&context.valid, memory_order_relaxed);
    bool free_right = atomic_load_explicit(&context.free_right, memory_order_relaxed);
    free(owned);
    valid = valid && discovered_count <= shard->n &&
            kh_wire_write_response(STDOUT_FILENO, 0, discovered_count) &&
            kh_wire_write_u32(STDOUT_FILENO, free_right);
    for (uint32_t index = 0; valid && index < thread_count; ++index) {
        for (discovery_chunk_t *chunk = discovered[index].first;
             valid && chunk != NULL; chunk = chunk->next) {
            valid = kh_wire_write_u32s(STDOUT_FILENO, chunk->items, chunk->count);
        }
    }
    for (uint32_t index = 0; index < thread_count; ++index) {
        discovery_free(&discovered[index]);
    }
    free(discovered);
    if (!valid && *error == NULL) {
        *error = "parallel native shard level failed";
    }
    return valid;
}

typedef struct {
    shard_t *shard;
    bool terminal_owned;
    uint32_t shortest;
    uint32_t root_begin;
    assignment_t *proposals;
    unsigned char *used_left;
    unsigned char *used_right;
    uint32_t proposal_capacity;
    uint32_t proposal_count;
    pthread_mutex_t commit;
    _Atomic uint64_t scans;
    _Atomic bool valid;
    _Atomic bool full;
} propose_context_t;

static void propose_range(void *raw, uint64_t begin, uint64_t end,
                          uint32_t worker_index) {
    (void)worker_index;
    propose_context_t *context = raw;
    shard_t *shard = context->shard;
    size_t stack_size = (size_t)context->shortest + 1;
    uint32_t *stack_left = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_cursor = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_right = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_choice = malloc(stack_size * sizeof(uint32_t));
    if (stack_left == NULL || stack_cursor == NULL || stack_right == NULL ||
        stack_choice == NULL) {
        atomic_store_explicit(&context->valid, false, memory_order_relaxed);
        free(stack_left); free(stack_cursor); free(stack_right); free(stack_choice);
        return;
    }
    uint64_t local_scans = 0;
    for (uint64_t position = begin; position < end &&
         atomic_load_explicit(&context->valid, memory_order_relaxed) &&
         !atomic_load_explicit(&context->full, memory_order_relaxed); ++position) {
        uint32_t root = context->root_begin + (uint32_t)position;
        if (root % shard->workers != shard->index ||
            shard->left[root] != ABSENT || shard->distance[root] != 0) {
            continue;
        }
        uint32_t depth = 0;
        stack_left[0] = root;
        stack_cursor[0] = 0;
        bool found = false;
        bool valid = true;
        while (valid) {
            uint32_t left = stack_left[depth];
            bool descended = false;
            while (stack_cursor[depth] < shard->f) {
                uint32_t choice = stack_cursor[depth]++;
                uint32_t right;
                ++local_scans;
                if (!kh_shard_neighbor(shard->graph, left, choice, &right) ||
                    right >= shard->q) {
                    valid = false;
                    break;
                }
                uint32_t mate = shard->right[right];
                if (mate == ABSENT && (!context->terminal_owned ||
                    right % shard->workers == shard->index) &&
                    shard->distance[left] + 1 == context->shortest) {
                    stack_right[depth] = right;
                    stack_choice[depth] = choice;
                    found = true;
                    break;
                }
                if (mate != ABSENT &&
                    shard->distance[mate] == shard->distance[left] + 1 &&
                    shard->distance[mate] < context->shortest) {
                    stack_right[depth] = right;
                    stack_choice[depth] = choice;
                    ++depth;
                    if (depth >= stack_size) {
                        valid = false;
                        break;
                    }
                    stack_left[depth] = mate;
                    stack_cursor[depth] = 0;
                    descended = true;
                    break;
                }
            }
            if (!valid || found) break;
            if (descended) continue;
            if (depth == 0) break;
            --depth;
        }
        if (!valid) {
            atomic_store_explicit(&context->valid, false, memory_order_relaxed);
            break;
        }
        if (!found) continue;
        pthread_mutex_lock(&context->commit);
        bool no_space = (uint64_t)context->proposal_count + depth + 1 >
                        context->proposal_capacity;
        bool conflict = no_space;
        for (uint32_t item = 0; !conflict && item <= depth; ++item) {
            uint32_t left = stack_left[item];
            uint32_t right = stack_right[item];
            conflict = bit_test(context->used_left, left) ||
                ((context->used_right[right >> 3] >> (right & 7)) & 1U);
        }
        if (!conflict) {
            for (uint32_t item = 0; item <= depth; ++item) {
                uint32_t left = stack_left[item];
                uint32_t right = stack_right[item];
                bit_set(context->used_left, left);
                context->used_right[right >> 3] |=
                    (unsigned char)(1U << (right & 7));
                context->proposals[context->proposal_count++] = (assignment_t){
                    left, right, stack_choice[item] | (item == 0 ? PATH_START : 0)};
            }
        }
        if (no_space) {
            atomic_store_explicit(&context->full, true, memory_order_relaxed);
        }
        pthread_mutex_unlock(&context->commit);
    }
    atomic_fetch_add_explicit(&context->scans, local_scans, memory_order_relaxed);
    free(stack_left); free(stack_cursor); free(stack_right); free(stack_choice);
}

static bool handle_propose(shard_t *shard, uint64_t count) {
    uint32_t shortest;
    uint32_t root_begin;
    uint32_t root_end;
    if (count > 1 || !kh_wire_read_u32(STDIN_FILENO, &shortest) ||
        !kh_wire_read_u32(STDIN_FILENO, &root_begin) ||
        !kh_wire_read_u32(STDIN_FILENO, &root_end) ||
        shortest == 0 || shortest > shard->n || root_begin > root_end || root_end > shard->n) {
        return false;
    }
    uint32_t proposal_capacity = shard->n < KH_SHARD_PROPOSAL_LIMIT
                                 ? shard->n : KH_SHARD_PROPOSAL_LIMIT;
    if (shortest > proposal_capacity) {
        return false;
    }
    unsigned char *used_left = calloc(((size_t)shard->n + 7) / 8, 1);
    unsigned char *used_right = calloc(((uint64_t)shard->q + 7) / 8, 1);
    assignment_t *proposals = malloc((size_t)proposal_capacity * sizeof *proposals);
    if (used_left == NULL || used_right == NULL || proposals == NULL) {
        free(used_left); free(used_right); free(proposals);
        return false;
    }
    propose_context_t context = {
        .shard = shard,
        .terminal_owned = count != 0,
        .shortest = shortest,
        .root_begin = root_begin,
        .proposals = proposals,
        .used_left = used_left,
        .used_right = used_right,
        .proposal_capacity = proposal_capacity,
        .proposal_count = 0,
        .scans = 0,
        .valid = true,
        .full = false,
    };
    if (pthread_mutex_init(&context.commit, NULL) != 0) {
        free(used_left); free(used_right); free(proposals);
        return false;
    }
    kh_shard_parallel(shard->graph, root_end - root_begin, propose_range, &context);
    bool valid = atomic_load_explicit(&context.valid, memory_order_relaxed);
    uint64_t scans = atomic_load_explicit(&context.scans, memory_order_relaxed);
    valid = valid && kh_wire_write_response(STDOUT_FILENO, 0, context.proposal_count) &&
            kh_wire_write_u64(STDOUT_FILENO, scans);
    for (uint32_t position = 0; valid && position < context.proposal_count; ++position) {
        valid = kh_wire_write_u32(STDOUT_FILENO, proposals[position].left) &&
                kh_wire_write_u32(STDOUT_FILENO, proposals[position].right) &&
                kh_wire_write_u32(STDOUT_FILENO, proposals[position].choice);
    }
    pthread_mutex_destroy(&context.commit);
    free(used_left); free(used_right); free(proposals);
    return valid;
}

static bool handle_apply(shard_t *shard, uint64_t count) {
    if (count > shard->n || count > SIZE_MAX / sizeof(assignment_t)) {
        return false;
    }
    assignment_t *assignments = malloc((size_t)count * sizeof *assignments);
    bool valid = count == 0 || assignments != NULL;
    memset(shard->seen_left, 0, ((size_t)shard->n + 7) / 8);
    memset(shard->right_bits, 0, ((uint64_t)shard->q + 7) / 8);
    for (uint64_t position = 0; valid && position < count; ++position) {
        assignment_t *item = &assignments[position];
        uint32_t actual;
        valid = kh_wire_read_u32(STDIN_FILENO, &item->left) &&
                kh_wire_read_u32(STDIN_FILENO, &item->right) &&
                kh_wire_read_u32(STDIN_FILENO, &item->choice) &&
                item->left < shard->n && item->right < shard->q &&
                item->choice < shard->f && !bit_test(shard->seen_left, item->left) &&
                !((shard->right_bits[item->right >> 3] >> (item->right & 7)) & 1U) &&
                kh_shard_neighbor(shard->graph, item->left, item->choice, &actual) &&
                actual == item->right;
        if (valid) {
            bit_set(shard->seen_left, item->left);
            shard->right_bits[item->right >> 3] |= (unsigned char)(1U << (item->right & 7));
        }
    }
    for (uint64_t position = 0; valid && position < count; ++position) {
        uint32_t old = shard->left[assignments[position].left];
        if (old != ABSENT) {
            if (old >= shard->q || shard->right[old] != assignments[position].left) {
                valid = false;
                break;
            }
            shard->right[old] = ABSENT;
        }
    }
    for (uint64_t position = 0; valid && position < count; ++position) {
        assignment_t item = assignments[position];
        if (shard->right[item.right] != ABSENT) {
            valid = false;
            break;
        }
        shard->left[item.left] = item.right;
        shard->choice[item.left] = item.choice;
        shard->right[item.right] = item.left;
    }
    free(assignments);
    return valid && kh_wire_write_response(STDOUT_FILENO, 0, 0);
}

static bool handle_hall(const shard_t *shard, uint64_t count) {
    uint64_t bytes = ((uint64_t)shard->q + 7) / 8;
    return count == 0 && kh_wire_write_response(STDOUT_FILENO, 0, bytes) &&
           kh_wire_write(STDOUT_FILENO, shard->right_bits, (size_t)bytes);
}

static int fail(const char *message) {
    fprintf(stderr, "kh_match_worker: %s\n", message);
    return 1;
}

int main(int argc, char **argv) {
    uint32_t index = UINT32_MAX;
    uint32_t workers = 0;
    const char *cpus = NULL;
    for (int position = 1; position < argc; ++position) {
        if (position + 1 >= argc) {
            return fail("missing option value");
        }
        const char *option = argv[position++];
        char *end = NULL;
        if (!strcmp(option, "--cpus")) {
            cpus = argv[position];
            continue;
        }
        unsigned long value = strtoul(argv[position], &end, 10);
        if (*argv[position] == '\0' || *end != '\0' || value > UINT32_MAX) {
            return fail("invalid numeric option");
        }
        if (!strcmp(option, "--index")) {
            index = (uint32_t)value;
        } else if (!strcmp(option, "--count")) {
            workers = (uint32_t)value;
        } else {
            return fail("unknown option");
        }
    }
    if (workers < 2 || workers > 256 || index >= workers || !apply_affinity(cpus)) {
        return fail("invalid shard identity or CPU affinity");
    }
    kh_wire_header_t header;
    init_t init = {0};
    if (!kh_wire_read_request(STDIN_FILENO, &header) || header.operation != KH_WIRE_INIT ||
        !read_init(STDIN_FILENO, header.count, &init)) {
        init_free(&init);
        return fail("invalid initialization frame");
    }
    const char *error = NULL;
    shard_t shard = {0};
    bool created = shard_create(&shard, index, workers, &init, &error);
    init_free(&init);
    if (!created) {
        kh_wire_write_response(STDOUT_FILENO, 1, 0);
        shard_free(&shard);
        return fail(error == NULL ? "native shard initialization failed" : error);
    }
    if (!kh_wire_write_response(STDOUT_FILENO, 0, 3) ||
        !kh_wire_write_u32(STDOUT_FILENO, shard.n) ||
        !kh_wire_write_u32(STDOUT_FILENO, shard.q) ||
        !kh_wire_write_u32(STDOUT_FILENO, shard.f)) {
        shard_free(&shard);
        return fail("initialization response failed");
    }
    int result = 0;
    for (;;) {
        if (!kh_wire_read_request(STDIN_FILENO, &header)) {
            result = fail("request stream ended unexpectedly");
            break;
        }
        if (header.operation == KH_WIRE_STOP && header.count == 0) {
            uint64_t cpu_microseconds;
            uint64_t peak_rss_bytes;
            bool measured = kh_resource_snapshot(&cpu_microseconds, &peak_rss_bytes);
            if (!kh_wire_write_response(STDOUT_FILENO, measured ? 0 : 1, measured ? 2 : 0) ||
                (measured && (!kh_wire_write_u64(STDOUT_FILENO, cpu_microseconds) ||
                              !kh_wire_write_u64(STDOUT_FILENO, peak_rss_bytes)))) {
                result = fail("resource usage response failed");
            }
            break;
        }
        bool valid = false;
        if (header.operation == KH_WIRE_SCAN) {
            valid = handle_scan(&shard, header.count, &error);
        } else if (header.operation == KH_WIRE_LEVEL) {
            valid = handle_level(&shard, header.count, &error);
        } else if (header.operation == KH_WIRE_PROPOSE) {
            valid = handle_propose(&shard, header.count);
        } else if (header.operation == KH_WIRE_APPLY) {
            valid = handle_apply(&shard, header.count);
        } else if (header.operation == KH_WIRE_HALL) {
            valid = handle_hall(&shard, header.count);
        }
        if (!valid) {
            result = fail(error == NULL ? "native distributed operation failed" : error);
            break;
        }
    }
    shard_free(&shard);
    return result;
}
