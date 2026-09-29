#define _GNU_SOURCE

#include "kh_shard.h"
#include "kh_resource.h"
#include "kh_wire.h"

#include <errno.h>
#include <inttypes.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define ABSENT UINT32_MAX
#define PATH_START UINT32_C(0x80000000)
#define SCAN_TARGET_BYTES UINT64_C(1048576)

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
    uint32_t *choice;
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
    uint64_t state_required = 42 * (uint64_t)shard->n +
                              8 * (uint64_t)shard->q +
                              ((uint64_t)shard->q + 3) / 4 +
                              UINT64_C(67108864);
    if (state_required > init->max_bytes) {
        *error = "native shard replicated state exceeds memory limit";
        return false;
    }
    shard->left = malloc((size_t)shard->n * sizeof(uint32_t));
    shard->right = malloc((size_t)shard->q * sizeof(uint32_t));
    shard->choice = malloc((size_t)shard->n * sizeof(uint32_t));
    shard->distance = malloc((size_t)shard->n * sizeof(uint32_t));
    shard->seen_left = malloc(shard->n);
    shard->right_bits = calloc(((uint64_t)shard->q + 7) / 8, 1);
    if (shard->left == NULL || shard->right == NULL || shard->choice == NULL ||
        shard->distance == NULL || shard->seen_left == NULL || shard->right_bits == NULL) {
        *error = "cannot allocate native shard matching state";
        return false;
    }
    memset(shard->left, 255, (size_t)shard->n * sizeof(uint32_t));
    memset(shard->right, 255, (size_t)shard->q * sizeof(uint32_t));
    memset(shard->choice, 255, (size_t)shard->n * sizeof(uint32_t));
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
    for (uint64_t position = 0; valid && position < count; ++position) {
        valid = kh_wire_read_u32(STDIN_FILENO, &left[position]) &&
                left[position] < shard->n && left[position] % shard->workers == shard->index;
    }
    valid = valid && kh_shard_scan(shard->graph, left, count, labels, error);
    uint64_t label_count = count * shard->f;
    valid = valid && kh_wire_write_response(STDOUT_FILENO, 0, label_count);
    for (uint64_t position = 0; valid && position < label_count; ++position) {
        valid = kh_wire_write_u32(STDOUT_FILENO, labels[position]);
    }
    free(left);
    free(labels);
    return valid;
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
    for (uint64_t position = 0; position < count; ++position) {
        uint32_t left;
        if (!kh_wire_read_u32(STDIN_FILENO, &left) || left >= shard->n) {
            return false;
        }
        shard->distance[left] = depth;
        owned_count += left % shard->workers == shard->index;
    }
    // The frontier must be replayed for owned extraction, so the coordinator
    // sends canonical sorted frontiers and ownership is an arithmetic sequence.
    // Receive it once into a bounded full-level array when this shard has work.
    // The request has already been consumed; reconstruct owned vertices from
    // distance changes at this exact depth.
    uint32_t *owned = malloc((size_t)owned_count * sizeof(uint32_t));
    uint32_t *discovered = malloc((size_t)shard->n * sizeof(uint32_t));
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
    memset(shard->seen_left, 0, shard->n);
    uint64_t batch = SCAN_TARGET_BYTES / (4 * (uint64_t)shard->f);
    if (batch == 0) {
        batch = 1;
    }
    uint32_t discovered_count = 0;
    bool free_right = false;
    bool valid = true;
    for (uint64_t begin = 0; valid && begin < owned_count; begin += batch) {
        uint64_t take = owned_count - begin < batch ? owned_count - begin : batch;
        uint32_t *labels = malloc((size_t)take * shard->f * sizeof(uint32_t));
        valid = labels != NULL &&
                kh_shard_scan(shard->graph, owned + begin, take, labels, error);
        for (uint64_t item = 0; valid && item < take; ++item) {
            for (uint32_t choice = 0; choice < shard->f; ++choice) {
                uint32_t right = labels[item * shard->f + choice];
                if (right >= shard->q) {
                    valid = false;
                    break;
                }
                shard->right_bits[right >> 3] |= (unsigned char)(1U << (right & 7));
                uint32_t mate = shard->right[right];
                if (mate == ABSENT) {
                    free_right = true;
                } else if (shard->distance[mate] == ABSENT && !shard->seen_left[mate]) {
                    shard->seen_left[mate] = 1;
                    discovered[discovered_count++] = mate;
                }
            }
        }
        free(labels);
    }
    free(owned);
    valid = valid && kh_wire_write_response(STDOUT_FILENO, 0, discovered_count) &&
            kh_wire_write_u32(STDOUT_FILENO, free_right);
    for (uint32_t position = 0; valid && position < discovered_count; ++position) {
        valid = kh_wire_write_u32(STDOUT_FILENO, discovered[position]);
    }
    free(discovered);
    return valid;
}

static bool handle_propose(shard_t *shard, uint64_t count) {
    uint32_t shortest;
    if (count > 1 || !kh_wire_read_u32(STDIN_FILENO, &shortest) ||
        shortest == 0 || shortest > shard->n) {
        return false;
    }
    size_t stack_size = (size_t)shortest + 1;
    uint32_t *stack_left = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_cursor = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_right = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_choice = malloc(stack_size * sizeof(uint32_t));
    unsigned char *used_left = calloc(shard->n, 1);
    unsigned char *used_right = calloc(((uint64_t)shard->q + 7) / 8, 1);
    assignment_t *proposals = malloc((size_t)shard->n * sizeof *proposals);
    if (stack_left == NULL || stack_cursor == NULL || stack_right == NULL ||
        stack_choice == NULL || used_left == NULL || used_right == NULL ||
        proposals == NULL) {
        free(stack_left);
        free(stack_cursor);
        free(stack_right);
        free(stack_choice);
        free(used_left);
        free(used_right);
        free(proposals);
        return false;
    }
    uint32_t proposal_count = 0;
    uint64_t scans = 0;
    bool valid = true;
    for (uint32_t root = shard->index; valid && root < shard->n; root += shard->workers) {
        if (shard->left[root] != ABSENT || used_left[root] || shard->distance[root] != 0) {
            continue;
        }
        uint32_t depth = 0;
        stack_left[0] = root;
        stack_cursor[0] = 0;
        bool found = false;
        while (valid) {
            uint32_t left = stack_left[depth];
            bool descended = false;
            while (stack_cursor[depth] < shard->f) {
                uint32_t choice = stack_cursor[depth]++;
                uint32_t right;
                ++scans;
                if (!kh_shard_neighbor(shard->graph, left, choice, &right) || right >= shard->q) {
                    valid = false;
                    break;
                }
                if ((used_right[right >> 3] >> (right & 7)) & 1U) {
                    continue;
                }
                uint32_t mate = shard->right[right];
                // Terminal ownership makes free-right proposals from different
                // shards disjoint before the coordinator merge.
                if (mate == ABSENT && (!count || right % shard->workers == shard->index) &&
                    shard->distance[left] + 1 == shortest) {
                    stack_right[depth] = right;
                    stack_choice[depth] = choice;
                    found = true;
                    break;
                }
                if (mate != ABSENT && !used_left[mate] &&
                    shard->distance[mate] == shard->distance[left] + 1 &&
                    shard->distance[mate] < shortest) {
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
            if (!valid || found) {
                break;
            }
            if (descended) {
                continue;
            }
            if (depth == 0) {
                break;
            }
            --depth;
        }
        if (valid && found) {
            if ((uint64_t)proposal_count + depth + 1 > shard->n) {
                valid = false;
                break;
            }
            for (uint32_t position = 0; position <= depth; ++position) {
                uint32_t left = stack_left[position];
                uint32_t right = stack_right[position];
                used_left[left] = 1;
                used_right[right >> 3] |= (unsigned char)(1U << (right & 7));
                proposals[proposal_count++] = (assignment_t){
                    left, right, stack_choice[position] | (position == 0 ? PATH_START : 0)};
            }
        }
    }
    valid = valid && kh_wire_write_response(STDOUT_FILENO, 0, proposal_count) &&
            kh_wire_write_u64(STDOUT_FILENO, scans);
    for (uint32_t position = 0; valid && position < proposal_count; ++position) {
        valid = kh_wire_write_u32(STDOUT_FILENO, proposals[position].left) &&
                kh_wire_write_u32(STDOUT_FILENO, proposals[position].right) &&
                kh_wire_write_u32(STDOUT_FILENO, proposals[position].choice);
    }
    free(stack_left);
    free(stack_cursor);
    free(stack_right);
    free(stack_choice);
    free(used_left);
    free(used_right);
    free(proposals);
    return valid;
}

static bool handle_apply(shard_t *shard, uint64_t count) {
    if (count > shard->n || count > SIZE_MAX / sizeof(assignment_t)) {
        return false;
    }
    assignment_t *assignments = malloc((size_t)count * sizeof *assignments);
    bool valid = count == 0 || assignments != NULL;
    memset(shard->seen_left, 0, shard->n);
    memset(shard->right_bits, 0, ((uint64_t)shard->q + 7) / 8);
    for (uint64_t position = 0; valid && position < count; ++position) {
        assignment_t *item = &assignments[position];
        uint32_t actual;
        valid = kh_wire_read_u32(STDIN_FILENO, &item->left) &&
                kh_wire_read_u32(STDIN_FILENO, &item->right) &&
                kh_wire_read_u32(STDIN_FILENO, &item->choice) &&
                item->left < shard->n && item->right < shard->q &&
                item->choice < shard->f && !shard->seen_left[item->left] &&
                !((shard->right_bits[item->right >> 3] >> (item->right & 7)) & 1U) &&
                kh_shard_neighbor(shard->graph, item->left, item->choice, &actual) &&
                actual == item->right;
        if (valid) {
            shard->seen_left[item->left] = 1;
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
