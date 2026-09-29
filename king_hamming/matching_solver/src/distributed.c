#define _GNU_SOURCE

#include "kh_sha256.h"
#include "kh_resource.h"
#include "kh_solver.h"
#include "kh_wire.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define ABSENT UINT32_MAX
#define MAX_WORKERS 256
#define SCAN_TARGET_BYTES UINT64_C(1048576)

typedef struct {
    uint32_t p;
    uint32_t r;
    uint32_t q;
    uint32_t f;
    uint32_t budget;
    uint64_t theta;
    uint32_t block_count;
    uint16_t *stripes;
    uint32_t *copies;
    uint32_t count;
    unsigned char digest[32];
} dp_t;

typedef struct {
    int read_descriptor;
    int write_descriptor;
    uint32_t index;
} worker_t;

typedef struct {
    uint32_t right;
    uint32_t choice;
    uint32_t mate;
} edge_t;

typedef struct {
    uint32_t left;
    uint32_t right;
    uint32_t choice;
} assignment_t;

typedef struct {
    uint32_t *left;
    uint32_t *right;
    uint32_t *choice;
    uint32_t *distance;
    uint64_t *offsets;
    uint32_t *frontier;
    uint32_t *next;
    uint32_t matched;
    uint64_t phase;
    uint64_t scans;
} matching_t;

typedef struct {
    FILE *spool;
    unsigned char *right_bits;
    uint32_t shortest;
    uint32_t root_count;
} layer_t;

typedef struct {
    worker_t *worker;
    const uint32_t *left;
    uint64_t count;
    uint32_t f;
    uint32_t *labels;
    bool valid;
} scan_task_t;

typedef struct {
    worker_t *worker;
    const dp_t *dp;
    const uint32_t *polynomial;
    uint32_t threads;
    uint64_t max_bytes;
    bool valid;
} init_task_t;

typedef struct {
    worker_t *worker;
    const uint32_t *frontier;
    uint32_t frontier_count;
    uint32_t depth;
    uint32_t n;
    uint32_t *discovered;
    uint32_t discovered_count;
    bool free_right;
    bool valid;
} level_task_t;

typedef struct {
    worker_t *worker;
    uint32_t shortest;
    uint32_t n;
    bool restrict_terminals;
    assignment_t *proposals;
    uint32_t proposal_count;
    uint64_t scans;
    bool valid;
} proposal_task_t;

typedef struct {
    worker_t *worker;
    const assignment_t *assignments;
    uint32_t count;
    bool valid;
} apply_task_t;

typedef struct {
    worker_t *worker;
    unsigned char *bits;
    uint64_t bytes;
    bool valid;
} hall_task_t;

typedef struct {
    FILE *file;
    kh_sha256_t checksum;
    bool valid;
} hashed_writer_t;

static void dp_free(dp_t *dp) {
    free(dp->stripes);
    free(dp->copies);
    memset(dp, 0, sizeof *dp);
}

static void matching_free(matching_t *matching) {
    free(matching->left);
    free(matching->right);
    free(matching->choice);
    free(matching->distance);
    free(matching->offsets);
    free(matching->frontier);
    free(matching->next);
    memset(matching, 0, sizeof *matching);
}

static uint32_t load_u32(const unsigned char bytes[4]) {
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
           (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static uint64_t load_u64(const unsigned char bytes[8]) {
    uint64_t value = 0;
    for (uint32_t index = 0; index < 8; ++index) {
        value |= (uint64_t)bytes[index] << (8 * index);
    }
    return value;
}

static void store_u32(unsigned char bytes[4], uint32_t value) {
    for (uint32_t index = 0; index < 4; ++index) {
        bytes[index] = (unsigned char)(value >> (8 * index));
    }
}

static void store_u64(unsigned char bytes[8], uint64_t value) {
    for (uint32_t index = 0; index < 8; ++index) {
        bytes[index] = (unsigned char)(value >> (8 * index));
    }
}

static bool read_file(const char *path, unsigned char **output, size_t *size) {
    struct stat status;
    if (stat(path, &status) != 0 || status.st_size < 0 ||
        (uint64_t)status.st_size > UINT64_C(16777216)) {
        return false;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return false;
    }
    unsigned char *bytes = malloc((size_t)status.st_size);
    bool valid = bytes != NULL &&
                 fread(bytes, 1, (size_t)status.st_size, file) == (size_t)status.st_size;
    valid = fclose(file) == 0 && valid;
    if (!valid) {
        free(bytes);
        return false;
    }
    *output = bytes;
    *size = (size_t)status.st_size;
    return true;
}

static bool varint_read(const unsigned char *bytes, size_t end, size_t *position,
                        uint64_t *output) {
    uint64_t value = 0;
    for (uint32_t shift = 0; shift < 64; shift += 7) {
        if (*position >= end) {
            return false;
        }
        unsigned char byte = bytes[(*position)++];
        if (shift == 63 && byte > 1) {
            return false;
        }
        value |= (uint64_t)(byte & 127) << shift;
        if (byte < 128) {
            if (shift != 0 && byte == 0) {
                return false;
            }
            *output = value;
            return true;
        }
    }
    return false;
}

static bool dp_read(const char *path, dp_t *dp) {
    unsigned char *bytes = NULL;
    size_t size = 0;
    if (!read_file(path, &bytes, &size) || size < 40 || memcmp(bytes, "KHD1", 4) != 0) {
        free(bytes);
        return false;
    }
    kh_sha256_t hash;
    unsigned char digest[32];
    kh_sha256_init(&hash);
    kh_sha256_update(&hash, bytes, size - 32);
    kh_sha256_final(&hash, digest);
    if (memcmp(digest, bytes + size - 32, 32) != 0) {
        free(bytes);
        return false;
    }
    kh_sha256_init(&hash);
    kh_sha256_update(&hash, bytes, size);
    kh_sha256_final(&hash, dp->digest);
    size_t position = 4;
    uint64_t p;
    uint64_t r;
    uint64_t blocks;
    kh_parameters_t parameters;
    const char *error = NULL;
    bool valid = varint_read(bytes, size - 32, &position, &p) &&
                 varint_read(bytes, size - 32, &position, &r) &&
                 varint_read(bytes, size - 32, &position, &dp->theta) &&
                 varint_read(bytes, size - 32, &position, &blocks) &&
                 p <= UINT32_MAX && r <= UINT32_MAX && blocks <= UINT32_MAX &&
                 kh_parameters((uint32_t)p, (uint32_t)r, &parameters, &error) &&
                 blocks > 0 && blocks <= parameters.budget;
    if (!valid) {
        free(bytes);
        return false;
    }
    dp->p = parameters.p;
    dp->r = parameters.r;
    dp->q = parameters.q;
    dp->f = parameters.f;
    dp->budget = parameters.budget;
    dp->block_count = (uint32_t)blocks;
    dp->stripes = calloc(dp->block_count, sizeof *dp->stripes);
    dp->copies = calloc(dp->block_count, sizeof *dp->copies);
    if (dp->stripes == NULL || dp->copies == NULL) {
        free(bytes);
        dp_free(dp);
        return false;
    }
    uint64_t stripes_total = 0;
    uint64_t cosets = 0;
    uint64_t gain = 0;
    uint64_t previous[3] = {0, 0, 0};
    for (uint32_t index = 0; valid && index < dp->block_count; ++index) {
        uint64_t a;
        uint64_t b;
        uint64_t t;
        uint64_t repeat;
        valid = varint_read(bytes, size - 32, &position, &a) &&
                varint_read(bytes, size - 32, &position, &b) &&
                varint_read(bytes, size - 32, &position, &t) &&
                varint_read(bytes, size - 32, &position, &repeat) &&
                a > 0 && a <= dp->p && b > 0 && b <= dp->p &&
                t > 0 && t <= dp->p && repeat > 0 && repeat <= dp->budget &&
                (index == 0 || previous[0] != a || previous[1] != b || previous[2] != t) &&
                t <= UINT32_MAX / repeat;
        if (!valid) {
            break;
        }
        uint64_t copies = t * repeat;
        valid = a <= dp->budget / copies && stripes_total + a * copies <= dp->budget &&
                cosets + copies + 1 <= dp->q - 1;
        if (!valid) {
            break;
        }
        bool residues[1621] = {0};
        uint32_t distinct = 0;
        for (uint32_t left = 0; left < a; ++left) {
            for (uint32_t right = 0; right < b; ++right) {
                uint32_t residue = (uint32_t)(((uint64_t)right * t + dp->p - left) % dp->p);
                if (!residues[residue]) {
                    residues[residue] = true;
                    ++distinct;
                }
            }
        }
        if (t > UINT64_MAX / distinct || t * distinct > UINT64_MAX / repeat ||
            gain > UINT64_MAX - t * distinct * repeat) {
            valid = false;
            break;
        }
        gain += t * distinct * repeat;
        dp->stripes[index] = (uint16_t)a;
        dp->copies[index] = (uint32_t)copies;
        stripes_total += a * copies;
        cosets += copies;
        previous[0] = a;
        previous[1] = b;
        previous[2] = t;
    }
    valid = valid && position == size - 32 && gain == dp->theta &&
            stripes_total > 0 && stripes_total <= UINT32_MAX / dp->f;
    if (valid) {
        dp->count = (uint32_t)(stripes_total * dp->f);
    }
    free(bytes);
    if (!valid) {
        dp_free(dp);
    }
    return valid;
}

static bool parse_u64(const char *text, uint64_t *output) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno != 0 || *text == '\0' || *end != '\0') {
        return false;
    }
    *output = (uint64_t)value;
    return true;
}

static bool polynomial_read(const char *text, const dp_t *dp, uint32_t *polynomial) {
    char *copy = strdup(text);
    if (copy == NULL) {
        return false;
    }
    char *cursor = copy;
    bool valid = true;
    for (uint32_t index = 0; index <= dp->r; ++index) {
        char *comma = strchr(cursor, ',');
        if (comma != NULL) {
            *comma = '\0';
        }
        uint64_t value;
        if (!parse_u64(cursor, &value) || value >= dp->p ||
            ((index < dp->r) != (comma != NULL))) {
            valid = false;
            break;
        }
        polynomial[index] = (uint32_t)value;
        if (comma != NULL) {
            cursor = comma + 1;
        }
    }
    free(copy);
    return valid && polynomial[dp->r] == 1 && polynomial[0] != 0;
}

static void *initialize_worker(void *raw) {
    init_task_t *task = raw;
    const dp_t *dp = task->dp;
    uint64_t payload = 4 * UINT64_C(5) + 8 + 4 * (uint64_t)(dp->r + 1) +
                       8 * (uint64_t)dp->block_count;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_INIT, payload) &&
                 kh_wire_write_u32(task->worker->write_descriptor, dp->p) &&
                 kh_wire_write_u32(task->worker->write_descriptor, dp->r) &&
                 kh_wire_write_u32(task->worker->write_descriptor, task->threads) &&
                 kh_wire_write_u32(task->worker->write_descriptor, dp->block_count) &&
                 kh_wire_write_u64(task->worker->write_descriptor, task->max_bytes) &&
                 kh_wire_write_u32(task->worker->write_descriptor, dp->r + 1);
    for (uint32_t index = 0; valid && index <= dp->r; ++index) {
        valid = kh_wire_write_u32(task->worker->write_descriptor, task->polynomial[index]);
    }
    for (uint32_t index = 0; valid && index < dp->block_count; ++index) {
        valid = kh_wire_write_u32(task->worker->write_descriptor, dp->stripes[index]) &&
                kh_wire_write_u32(task->worker->write_descriptor, dp->copies[index]);
    }
    uint32_t status;
    uint64_t count;
    uint32_t n;
    uint32_t q;
    uint32_t f;
    valid = valid && kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
            status == 0 && count == 3 &&
            kh_wire_read_u32(task->worker->read_descriptor, &n) &&
            kh_wire_read_u32(task->worker->read_descriptor, &q) &&
            kh_wire_read_u32(task->worker->read_descriptor, &f) &&
            n == dp->count && q == dp->q && f == dp->f;
    task->valid = valid;
    return NULL;
}

static bool workers_initialize(worker_t *workers, uint32_t worker_count, const dp_t *dp,
                               const uint32_t *polynomial, uint32_t threads,
                               uint64_t max_bytes) {
    pthread_t launched[MAX_WORKERS];
    init_task_t tasks[MAX_WORKERS];
    uint32_t created = 0;
    for (uint32_t index = 0; index < worker_count; ++index) {
        tasks[index] = (init_task_t){&workers[index], dp, polynomial, threads, max_bytes, false};
        if (pthread_create(&launched[index], NULL, initialize_worker, &tasks[index]) != 0) {
            break;
        }
        ++created;
    }
    bool valid = created == worker_count;
    for (uint32_t index = 0; index < created; ++index) {
        pthread_join(launched[index], NULL);
        valid = valid && tasks[index].valid;
    }
    return valid;
}

static void *scan_worker(void *raw) {
    scan_task_t *task = raw;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_SCAN, task->count);
    for (uint64_t index = 0; valid && index < task->count; ++index) {
        valid = kh_wire_write_u32(task->worker->write_descriptor, task->left[index]);
    }
    uint32_t status;
    uint64_t count;
    uint64_t expected = task->count * task->f;
    valid = valid && kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
            status == 0 && count == expected;
    for (uint64_t index = 0; valid && index < expected; ++index) {
        valid = kh_wire_read_u32(task->worker->read_descriptor, &task->labels[index]);
    }
    task->valid = valid;
    return NULL;
}

static bool scan_parallel(scan_task_t *tasks, uint32_t worker_count) {
    pthread_t launched[MAX_WORKERS];
    bool active[MAX_WORKERS] = {0};
    bool valid = true;
    for (uint32_t index = 0; index < worker_count; ++index) {
        if (tasks[index].count == 0) {
            tasks[index].valid = true;
            continue;
        }
        if (pthread_create(&launched[index], NULL, scan_worker, &tasks[index]) != 0) {
            valid = false;
            break;
        }
        active[index] = true;
    }
    for (uint32_t index = 0; index < worker_count; ++index) {
        if (active[index]) {
            pthread_join(launched[index], NULL);
            valid = valid && tasks[index].valid;
        }
    }
    return valid;
}

static bool launch_all(void *tasks, size_t stride, uint32_t count,
                       void *(*function)(void *), size_t valid_offset) {
    pthread_t launched[MAX_WORKERS];
    uint32_t created = 0;
    unsigned char *bytes = tasks;
    for (uint32_t index = 0; index < count; ++index) {
        if (pthread_create(&launched[index], NULL, function, bytes + index * stride) != 0) {
            break;
        }
        ++created;
    }
    bool valid = created == count;
    for (uint32_t index = 0; index < created; ++index) {
        pthread_join(launched[index], NULL);
        bool *task_valid = (bool *)(void *)(bytes + index * stride + valid_offset);
        valid = valid && *task_valid;
    }
    return valid;
}

static void *level_worker(void *raw) {
    level_task_t *task = raw;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_LEVEL,
                                       task->frontier_count) &&
                 kh_wire_write_u32(task->worker->write_descriptor, task->depth);
    for (uint32_t index = 0; valid && index < task->frontier_count; ++index) {
        valid = kh_wire_write_u32(task->worker->write_descriptor, task->frontier[index]);
    }
    uint32_t status;
    uint64_t count;
    uint32_t free_right;
    valid = valid && kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
            status == 0 && count <= task->n &&
            kh_wire_read_u32(task->worker->read_descriptor, &free_right) && free_right <= 1;
    if (valid && count != 0) {
        task->discovered = malloc((size_t)count * sizeof(uint32_t));
        valid = task->discovered != NULL;
    }
    for (uint64_t index = 0; valid && index < count; ++index) {
        valid = kh_wire_read_u32(task->worker->read_descriptor, &task->discovered[index]) &&
                task->discovered[index] < task->n;
    }
    if (valid) {
        task->discovered_count = (uint32_t)count;
        task->free_right = free_right != 0;
    }
    task->valid = valid;
    return NULL;
}

static void *proposal_worker(void *raw) {
    proposal_task_t *task = raw;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_PROPOSE,
                                       task->restrict_terminals) &&
                 kh_wire_write_u32(task->worker->write_descriptor, task->shortest);
    uint32_t status;
    uint64_t count;
    valid = valid && kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
            status == 0 && count <= task->n &&
            kh_wire_read_u64(task->worker->read_descriptor, &task->scans);
    if (valid && count != 0) {
        task->proposals = malloc((size_t)count * sizeof(assignment_t));
        valid = task->proposals != NULL;
    }
    for (uint64_t index = 0; valid && index < count; ++index) {
        valid = kh_wire_read_u32(task->worker->read_descriptor, &task->proposals[index].left) &&
                kh_wire_read_u32(task->worker->read_descriptor, &task->proposals[index].right) &&
                kh_wire_read_u32(task->worker->read_descriptor, &task->proposals[index].choice);
    }
    if (valid) {
        task->proposal_count = (uint32_t)count;
    }
    task->valid = valid;
    return NULL;
}

static void *apply_worker(void *raw) {
    apply_task_t *task = raw;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_APPLY,
                                       task->count);
    for (uint32_t index = 0; valid && index < task->count; ++index) {
        valid = kh_wire_write_u32(task->worker->write_descriptor, task->assignments[index].left) &&
                kh_wire_write_u32(task->worker->write_descriptor, task->assignments[index].right) &&
                kh_wire_write_u32(task->worker->write_descriptor, task->assignments[index].choice);
    }
    uint32_t status;
    uint64_t count;
    valid = valid && kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
            status == 0 && count == 0;
    task->valid = valid;
    return NULL;
}

static void *hall_worker(void *raw) {
    hall_task_t *task = raw;
    uint32_t status;
    uint64_t count;
    bool valid = kh_wire_write_request(task->worker->write_descriptor, KH_WIRE_HALL, 0) &&
                 kh_wire_read_response(task->worker->read_descriptor, &status, &count) &&
                 status == 0 && count == task->bytes &&
                 kh_wire_read(task->worker->read_descriptor, task->bits, (size_t)task->bytes);
    task->valid = valid;
    return NULL;
}

static bool workers_apply(worker_t *workers, uint32_t worker_count,
                          const assignment_t *assignments, uint32_t count) {
    apply_task_t tasks[MAX_WORKERS];
    for (uint32_t index = 0; index < worker_count; ++index) {
        tasks[index] = (apply_task_t){&workers[index], assignments, count, false};
    }
    return launch_all(tasks, sizeof tasks[0], worker_count, apply_worker,
                      offsetof(apply_task_t, valid));
}

static bool reduced_breadth_first(worker_t *workers, uint32_t worker_count,
                                  const dp_t *dp, matching_t *matching,
                                  uint64_t max_edges, uint32_t *shortest,
                                  const char **error) {
    memset(matching->distance, 255, (size_t)dp->count * sizeof(uint32_t));
    uint32_t frontier_count = 0;
    for (uint32_t left = 0; left < dp->count; ++left) {
        if (matching->left[left] == ABSENT) {
            matching->distance[left] = 0;
            matching->frontier[frontier_count++] = left;
        }
    }
    uint32_t *frontier = matching->frontier;
    uint32_t *next = matching->next;
    uint32_t depth = 0;
    uint64_t phase_scans = 0;
    *shortest = ABSENT;
    while (frontier_count != 0) {
        uint64_t level_edges = (uint64_t)frontier_count * dp->f;
        if (phase_scans + level_edges > max_edges) {
            *error = "distributed matching edge budget exceeded";
            return false;
        }
        level_task_t tasks[MAX_WORKERS];
        for (uint32_t index = 0; index < worker_count; ++index) {
            tasks[index] = (level_task_t){
                &workers[index], frontier, frontier_count, depth, dp->count,
                NULL, 0, false, false};
        }
        bool valid = launch_all(tasks, sizeof tasks[0], worker_count, level_worker,
                                offsetof(level_task_t, valid));
        uint32_t next_count = 0;
        bool found = false;
        for (uint32_t index = 0; index < worker_count; ++index) {
            if (valid) {
                found = found || tasks[index].free_right;
                for (uint32_t item = 0; item < tasks[index].discovered_count; ++item) {
                    uint32_t left = tasks[index].discovered[item];
                    if (matching->distance[left] == ABSENT) {
                        matching->distance[left] = depth + 1;
                        next[next_count++] = left;
                    }
                }
            }
            free(tasks[index].discovered);
        }
        if (!valid) {
            *error = "native reduced BFS worker failed";
            return false;
        }
        matching->scans += level_edges;
        phase_scans += level_edges;
        printf("{\"event\":\"bfs\",\"depth\":%u,\"frontier\":%u,\"edges\":%" PRIu64
               ",\"free_right_found\":%s,\"done\":%u,\"total\":%u,"
               "\"checkpoint_done\":%u,\"phase\":\"matching\",\"units\":\"requests\","
               "\"heartbeat\":true}\n",
               depth, frontier_count, level_edges, found ? "true" : "false",
               matching->matched, dp->count, matching->matched);
        fflush(stdout);
        if (found) {
            *shortest = depth + 1;
            return true;
        }
        uint32_t *swap = frontier;
        frontier = next;
        next = swap;
        frontier_count = next_count;
        ++depth;
    }
    return true;
}

static bool proposal_path_valid(const dp_t *dp, const matching_t *matching,
                                const assignment_t *path, uint32_t count,
                                uint32_t shortest) {
    if (count != shortest || count == 0 || path[0].left >= dp->count ||
        matching->left[path[0].left] != ABSENT ||
        matching->distance[path[0].left] != 0) {
        return false;
    }
    for (uint32_t index = 0; index < count; ++index) {
        const assignment_t *item = &path[index];
        if (item->left >= dp->count || item->right >= dp->q || item->choice >= dp->f ||
            matching->distance[item->left] != index) {
            return false;
        }
        uint32_t mate = matching->right[item->right];
        if (index + 1 == count) {
            if (mate != ABSENT) {
                return false;
            }
        } else if (mate != path[index + 1].left) {
            return false;
        }
    }
    return true;
}

static bool reduced_augment(worker_t *workers, uint32_t worker_count,
                            const dp_t *dp, matching_t *matching,
                            uint32_t shortest, uint32_t *paths,
                            const char **error) {
    proposal_task_t tasks[MAX_WORKERS];
    for (uint32_t index = 0; index < worker_count; ++index) {
        tasks[index] = (proposal_task_t){&workers[index], shortest, dp->count,
                                         matching->phase == 0, NULL, 0, 0, false};
    }
    bool valid = launch_all(tasks, sizeof tasks[0], worker_count, proposal_worker,
                            offsetof(proposal_task_t, valid));
    uint64_t proposed_scans = 0;
    uint64_t total_proposals = 0;
    for (uint32_t index = 0; index < worker_count; ++index) {
        proposed_scans += tasks[index].scans;
        total_proposals += tasks[index].proposal_count;
    }
    matching->scans += proposed_scans;
    assignment_t *accepted = valid ? malloc((size_t)dp->count * sizeof *accepted) : NULL;
    unsigned char *used_left = valid ? calloc(dp->count, 1) : NULL;
    unsigned char *used_right = valid ? calloc(((uint64_t)dp->q + 7) / 8, 1) : NULL;
    if (!valid || accepted == NULL || used_left == NULL || used_right == NULL) {
        valid = false;
    }
    uint32_t accepted_count = 0;
    *paths = 0;
    for (uint32_t worker = 0; valid && worker < worker_count; ++worker) {
        uint32_t begin = 0;
        while (begin < tasks[worker].proposal_count) {
            assignment_t *first = &tasks[worker].proposals[begin];
            if ((first->choice & UINT32_C(0x80000000)) == 0) {
                valid = false;
                break;
            }
            first->choice &= UINT32_C(0x7fffffff);
            uint32_t end = begin + 1;
            while (end < tasks[worker].proposal_count &&
                   (tasks[worker].proposals[end].choice & UINT32_C(0x80000000)) == 0) {
                ++end;
            }
            uint32_t length = end - begin;
            bool conflict = !proposal_path_valid(dp, matching, first, length, shortest);
            for (uint32_t index = begin; !conflict && index < end; ++index) {
                assignment_t item = tasks[worker].proposals[index];
                if (item.left >= dp->count || item.right >= dp->q ||
                    used_left[item.left] ||
                    ((used_right[item.right >> 3] >> (item.right & 7)) & 1U)) {
                    conflict = true;
                }
            }
            if (!conflict) {
                for (uint32_t index = begin; index < end; ++index) {
                    assignment_t item = tasks[worker].proposals[index];
                    used_left[item.left] = 1;
                    used_right[item.right >> 3] |= (unsigned char)(1U << (item.right & 7));
                    accepted[accepted_count++] = item;
                }
                ++*paths;
            }
            begin = end;
        }
    }
    if (valid && *paths == 0) {
        valid = false;
        *error = "native distributed proposals made no progress";
    }
    if (valid && !workers_apply(workers, worker_count, accepted, accepted_count)) {
        valid = false;
        *error = "native workers rejected an accepted matching delta";
    }
    if (valid) {
        for (uint32_t index = 0; index < accepted_count; ++index) {
            uint32_t old = matching->left[accepted[index].left];
            if (old != ABSENT) {
                matching->right[old] = ABSENT;
            }
        }
        for (uint32_t index = 0; index < accepted_count; ++index) {
            assignment_t item = accepted[index];
            matching->left[item.left] = item.right;
            matching->choice[item.left] = item.choice;
            matching->right[item.right] = item.left;
        }
        matching->matched += *paths;
    }
    for (uint32_t index = 0; index < worker_count; ++index) {
        free(tasks[index].proposals);
    }
    free(accepted);
    free(used_left);
    free(used_right);
    if (!valid && *error == NULL) {
        *error = "native distributed path proposal failed";
    }
    (void)total_proposals;
    return valid;
}

static bool reduced_hall(worker_t *workers, uint32_t worker_count, const dp_t *dp,
                         const matching_t *matching, uint32_t *left_count,
                         uint32_t *right_count, const char **error) {
    uint64_t bytes = ((uint64_t)dp->q + 7) / 8;
    unsigned char *combined = calloc((size_t)bytes, 1);
    hall_task_t tasks[MAX_WORKERS];
    bool valid = combined != NULL;
    for (uint32_t index = 0; index < worker_count; ++index) {
        tasks[index] = (hall_task_t){&workers[index], calloc((size_t)bytes, 1), bytes, false};
        valid = valid && tasks[index].bits != NULL;
    }
    valid = valid && launch_all(tasks, sizeof tasks[0], worker_count, hall_worker,
                                offsetof(hall_task_t, valid));
    *left_count = 0;
    *right_count = 0;
    if (valid) {
        for (uint32_t left = 0; left < dp->count; ++left) {
            *left_count += matching->distance[left] != ABSENT;
        }
        for (uint64_t byte = 0; byte < bytes; ++byte) {
            for (uint32_t worker = 0; worker < worker_count; ++worker) {
                combined[byte] |= tasks[worker].bits[byte];
            }
            *right_count += (uint32_t)__builtin_popcount((unsigned int)combined[byte]);
        }
        valid = *right_count < *left_count;
    }
    for (uint32_t index = 0; index < worker_count; ++index) {
        free(tasks[index].bits);
    }
    free(combined);
    if (!valid) {
        *error = "native distributed Hall closure is invalid";
    }
    return valid;
}

static bool workers_restore(worker_t *workers, uint32_t worker_count, const dp_t *dp,
                            const matching_t *matching) {
    assignment_t *assignments = malloc((size_t)matching->matched * sizeof *assignments);
    if (matching->matched != 0 && assignments == NULL) {
        return false;
    }
    uint32_t count = 0;
    for (uint32_t left = 0; left < dp->count; ++left) {
        if (matching->left[left] != ABSENT) {
            assignments[count++] = (assignment_t){left, matching->left[left], matching->choice[left]};
        }
    }
    bool valid = count == matching->matched &&
                 workers_apply(workers, worker_count, assignments, count);
    free(assignments);
    return valid;
}

static bool matching_create(const dp_t *dp, uint64_t max_bytes, matching_t *matching,
                            uint64_t *required) {
    uint64_t n = dp->count;
    uint64_t q = dp->q;
    // Persistent pairs/frontiers plus worst-case path stacks, claim maps,
    // one worker-bucket copy, bounded scan frames, and process headroom.
    *required = 52 * n + 4 * q + (q + 3) / 4 + UINT64_C(67108864);
    if (*required > max_bytes || n > SIZE_MAX / sizeof(uint64_t) ||
        q > SIZE_MAX / sizeof(uint32_t)) {
        return false;
    }
    matching->left = malloc((size_t)n * sizeof(uint32_t));
    matching->right = malloc((size_t)q * sizeof(uint32_t));
    matching->choice = malloc((size_t)n * sizeof(uint32_t));
    matching->distance = malloc((size_t)n * sizeof(uint32_t));
    matching->offsets = malloc((size_t)n * sizeof(uint64_t));
    matching->frontier = malloc((size_t)n * sizeof(uint32_t));
    matching->next = malloc((size_t)n * sizeof(uint32_t));
    if (matching->left == NULL || matching->right == NULL || matching->choice == NULL ||
        matching->distance == NULL || matching->offsets == NULL ||
        matching->frontier == NULL || matching->next == NULL) {
        matching_free(matching);
        return false;
    }
    memset(matching->left, 255, (size_t)n * sizeof(uint32_t));
    memset(matching->right, 255, (size_t)q * sizeof(uint32_t));
    memset(matching->choice, 255, (size_t)n * sizeof(uint32_t));
    return true;
}

static void __attribute__((unused)) layer_free(layer_t *layer) {
    if (layer->spool != NULL) {
        fclose(layer->spool);
    }
    free(layer->right_bits);
    memset(layer, 0, sizeof *layer);
}

static bool spool_edges(layer_t *layer, matching_t *matching, uint32_t left,
                        const edge_t *edges, uint32_t count) {
    if (count == 0) {
        return true;
    }
    off_t offset = ftello(layer->spool);
    if (offset < 0 || fwrite(&count, sizeof count, 1, layer->spool) != 1 ||
        fwrite(edges, sizeof *edges, count, layer->spool) != count) {
        return false;
    }
    matching->offsets[left] = (uint64_t)offset;
    return true;
}

static bool build_buckets(const uint32_t *frontier, uint32_t frontier_count,
                          uint32_t worker_count, uint32_t ***buckets_output,
                          uint32_t **counts_output) {
    uint32_t *counts = calloc(worker_count, sizeof *counts);
    uint32_t **buckets = calloc(worker_count, sizeof *buckets);
    uint32_t *positions = calloc(worker_count, sizeof *positions);
    if (counts == NULL || buckets == NULL || positions == NULL) {
        free(counts);
        free(buckets);
        free(positions);
        return false;
    }
    for (uint32_t index = 0; index < frontier_count; ++index) {
        ++counts[frontier[index] % worker_count];
    }
    bool valid = true;
    for (uint32_t index = 0; index < worker_count; ++index) {
        buckets[index] = malloc((size_t)counts[index] * sizeof(uint32_t));
        if (counts[index] != 0 && buckets[index] == NULL) {
            valid = false;
        }
    }
    for (uint32_t index = 0; valid && index < frontier_count; ++index) {
        uint32_t owner = frontier[index] % worker_count;
        buckets[owner][positions[owner]++] = frontier[index];
    }
    free(positions);
    if (!valid) {
        for (uint32_t index = 0; index < worker_count; ++index) {
            free(buckets[index]);
        }
        free(buckets);
        free(counts);
        return false;
    }
    *buckets_output = buckets;
    *counts_output = counts;
    return true;
}

static void free_buckets(uint32_t **buckets, uint32_t *counts, uint32_t worker_count) {
    if (buckets != NULL) {
        for (uint32_t index = 0; index < worker_count; ++index) {
            free(buckets[index]);
        }
    }
    free(buckets);
    free(counts);
}

static bool __attribute__((unused)) breadth_first(worker_t *workers, uint32_t worker_count, const dp_t *dp,
                          matching_t *matching, uint64_t max_edges, layer_t *layer,
                          const char **error) {
    layer->spool = tmpfile();
    layer->right_bits = calloc(((uint64_t)dp->q + 7) / 8, 1);
    layer->shortest = ABSENT;
    if (layer->spool == NULL || layer->right_bits == NULL) {
        *error = "cannot allocate native BFS layer";
        return false;
    }
    memset(matching->distance, 255, (size_t)dp->count * sizeof(uint32_t));
    memset(matching->offsets, 255, (size_t)dp->count * sizeof(uint64_t));
    uint32_t frontier_count = 0;
    for (uint32_t left = 0; left < dp->count; ++left) {
        if (matching->left[left] == ABSENT) {
            matching->distance[left] = 0;
            matching->frontier[frontier_count++] = left;
        }
    }
    layer->root_count = frontier_count;
    uint32_t *frontier = matching->frontier;
    uint32_t *next = matching->next;
    uint32_t depth = 0;
    uint64_t phase_scans = 0;
    uint64_t batch = SCAN_TARGET_BYTES / (4 * (uint64_t)dp->f);
    if (batch == 0) {
        batch = 1;
    }
    while (frontier_count != 0) {
        uint32_t **buckets = NULL;
        uint32_t *counts = NULL;
        if (!build_buckets(frontier, frontier_count, worker_count, &buckets, &counts)) {
            *error = "cannot allocate native BFS worker buckets";
            return false;
        }
        uint32_t positions[MAX_WORKERS] = {0};
        uint32_t next_count = 0;
        uint64_t level_edges = 0;
        bool found = false;
        bool valid = true;
        while (valid) {
            scan_task_t tasks[MAX_WORKERS] = {0};
            bool any = false;
            for (uint32_t index = 0; index < worker_count; ++index) {
                uint32_t remaining = counts[index] - positions[index];
                uint32_t take = remaining < batch ? remaining : (uint32_t)batch;
                tasks[index].worker = &workers[index];
                tasks[index].left = buckets[index] + positions[index];
                tasks[index].count = take;
                tasks[index].f = dp->f;
                if (take != 0) {
                    tasks[index].labels = malloc((size_t)take * dp->f * sizeof(uint32_t));
                    if (tasks[index].labels == NULL) {
                        valid = false;
                        break;
                    }
                    any = true;
                    positions[index] += take;
                }
            }
            if (valid && !any) {
                for (uint32_t index = 0; index < worker_count; ++index) {
                    free(tasks[index].labels);
                }
                break;
            }
            uint64_t round_edges = 0;
            for (uint32_t index = 0; index < worker_count; ++index) {
                round_edges += tasks[index].count * dp->f;
            }
            if (valid && phase_scans + round_edges > max_edges) {
                *error = "distributed matching edge budget exceeded";
                valid = false;
            }
            valid = valid && scan_parallel(tasks, worker_count);
            edge_t *edges = valid ? malloc((size_t)dp->f * sizeof *edges) : NULL;
            if (valid && edges == NULL) {
                valid = false;
            }
            for (uint32_t index = 0; valid && index < worker_count; ++index) {
                for (uint64_t item = 0; valid && item < tasks[index].count; ++item) {
                    uint32_t left = tasks[index].left[item];
                    uint32_t edge_count = 0;
                    for (uint32_t choice = 0; choice < dp->f; ++choice) {
                        uint32_t right = tasks[index].labels[item * dp->f + choice];
                        if (right >= dp->q) {
                            *error = "native shard returned a right label outside the field";
                            valid = false;
                            break;
                        }
                        layer->right_bits[right >> 3] |= (unsigned char)(1U << (right & 7));
                        uint32_t mate = matching->right[right];
                        if (mate == ABSENT) {
                            found = true;
                            edges[edge_count++] = (edge_t){right, choice, mate};
                        } else {
                            if (matching->distance[mate] == ABSENT) {
                                matching->distance[mate] = depth + 1;
                                next[next_count++] = mate;
                            }
                            if (matching->distance[mate] == depth + 1) {
                                edges[edge_count++] = (edge_t){right, choice, mate};
                            }
                        }
                    }
                    valid = valid && spool_edges(layer, matching, left, edges, edge_count);
                }
            }
            free(edges);
            for (uint32_t index = 0; index < worker_count; ++index) {
                free(tasks[index].labels);
            }
            matching->scans += round_edges;
            phase_scans += round_edges;
            level_edges += round_edges;
        }
        free_buckets(buckets, counts, worker_count);
        if (!valid) {
            if (*error == NULL) {
                *error = "native shard scan failed";
            }
            return false;
        }
        printf("{\"event\":\"bfs\",\"depth\":%u,\"frontier\":%u,\"edges\":%" PRIu64
               ",\"free_right_found\":%s,\"done\":%u,\"total\":%u,"
               "\"checkpoint_done\":%u,\"phase\":\"matching\",\"units\":\"requests\","
               "\"heartbeat\":true}\n",
               depth, frontier_count, level_edges, found ? "true" : "false",
               matching->matched, dp->count, matching->matched);
        fflush(stdout);
        if (found) {
            layer->shortest = depth + 1;
            break;
        }
        uint32_t *swap = frontier;
        frontier = next;
        next = swap;
        frontier_count = next_count;
        ++depth;
    }
    return true;
}

static bool spool_count(const matching_t *matching, FILE *spool, uint32_t left,
                        uint32_t *count) {
    if (matching->offsets[left] == UINT64_MAX) {
        *count = 0;
        return true;
    }
    if (matching->offsets[left] > INT64_MAX ||
        fseeko(spool, (off_t)matching->offsets[left], SEEK_SET) != 0) {
        return false;
    }
    return fread(count, sizeof *count, 1, spool) == 1;
}

static bool spool_edge(const matching_t *matching, FILE *spool, uint32_t left,
                       uint32_t index, edge_t *edge) {
    uint64_t position = matching->offsets[left] + sizeof(uint32_t) +
                        (uint64_t)index * sizeof *edge;
    if (position > INT64_MAX || fseeko(spool, (off_t)position, SEEK_SET) != 0) {
        return false;
    }
    return fread(edge, sizeof *edge, 1, spool) == 1;
}

static bool __attribute__((unused)) augment_layer(const dp_t *dp, matching_t *matching, layer_t *layer,
                          uint32_t *paths, const char **error) {
    *paths = 0;
    if (layer->shortest == ABSENT || layer->shortest > dp->count) {
        *error = "invalid native shortest-path layer";
        return false;
    }
    size_t stack_size = (size_t)layer->shortest + 1;
    uint32_t *stack_left = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_index = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_count = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_right = malloc(stack_size * sizeof(uint32_t));
    uint32_t *stack_choice = malloc(stack_size * sizeof(uint32_t));
    unsigned char *used_left = calloc(dp->count, 1);
    unsigned char *used_right = calloc(((uint64_t)dp->q + 7) / 8, 1);
    if (stack_left == NULL || stack_index == NULL || stack_count == NULL ||
        stack_right == NULL || stack_choice == NULL || used_left == NULL ||
        used_right == NULL) {
        free(stack_left);
        free(stack_index);
        free(stack_count);
        free(stack_right);
        free(stack_choice);
        free(used_left);
        free(used_right);
        *error = "cannot allocate native augmenting-path scratch";
        return false;
    }
    bool valid = true;
    for (uint32_t root = 0; valid && root < dp->count; ++root) {
        if (matching->left[root] != ABSENT || used_left[root]) {
            continue;
        }
        uint32_t depth = 0;
        stack_left[0] = root;
        stack_index[0] = 0;
        valid = spool_count(matching, layer->spool, root, &stack_count[0]);
        bool found = false;
        while (valid) {
            uint32_t left = stack_left[depth];
            if (stack_index[depth] >= stack_count[depth]) {
                if (depth == 0) {
                    break;
                }
                --depth;
                continue;
            }
            edge_t edge;
            valid = spool_edge(matching, layer->spool, left, stack_index[depth]++, &edge);
            if (!valid || edge.right >= dp->q || edge.choice >= dp->f ||
                (edge.mate != ABSENT && edge.mate >= dp->count)) {
                valid = false;
                break;
            }
            if ((used_right[edge.right >> 3] >> (edge.right & 7)) & 1U) {
                continue;
            }
            if (edge.mate == ABSENT) {
                if (matching->distance[left] + 1 != layer->shortest) {
                    continue;
                }
                stack_right[depth] = edge.right;
                stack_choice[depth] = edge.choice;
                found = true;
                break;
            }
            if (used_left[edge.mate] ||
                matching->distance[edge.mate] != matching->distance[left] + 1 ||
                matching->distance[edge.mate] >= layer->shortest) {
                continue;
            }
            stack_right[depth] = edge.right;
            stack_choice[depth] = edge.choice;
            ++depth;
            if (depth >= stack_size) {
                valid = false;
                break;
            }
            stack_left[depth] = edge.mate;
            stack_index[depth] = 0;
            valid = spool_count(matching, layer->spool, edge.mate, &stack_count[depth]);
        }
        if (valid && found) {
            for (uint32_t position = 0; position <= depth; ++position) {
                uint32_t left = stack_left[position];
                uint32_t right = stack_right[position];
                used_left[left] = 1;
                used_right[right >> 3] |= (unsigned char)(1U << (right & 7));
                matching->left[left] = right;
                matching->right[right] = left;
                matching->choice[left] = stack_choice[position];
            }
            ++matching->matched;
            ++*paths;
        }
    }
    free(stack_left);
    free(stack_index);
    free(stack_count);
    free(stack_right);
    free(stack_choice);
    free(used_left);
    free(used_right);
    if (!valid) {
        *error = "native augmenting-path spool is inconsistent";
    }
    return valid;
}

static bool hashed_write(hashed_writer_t *writer, const void *bytes, size_t count) {
    if (!writer->valid || fwrite(bytes, 1, count, writer->file) != count) {
        writer->valid = false;
        return false;
    }
    kh_sha256_update(&writer->checksum, bytes, count);
    return true;
}

static bool hashed_u32(hashed_writer_t *writer, uint32_t value) {
    unsigned char bytes[4];
    store_u32(bytes, value);
    return hashed_write(writer, bytes, sizeof bytes);
}

static bool hashed_u64(hashed_writer_t *writer, uint64_t value) {
    unsigned char bytes[8];
    store_u64(bytes, value);
    return hashed_write(writer, bytes, sizeof bytes);
}

static bool mkdir_existing(const char *path) {
    return mkdir(path, 0700) == 0 || errno == EEXIST;
}

static bool checkpoint_write(const char *directory, const dp_t *dp,
                             const uint32_t *polynomial, const matching_t *matching,
                             char **saved_path) {
    if (!mkdir_existing(directory)) {
        return false;
    }
    size_t needed = strlen(directory) + 64;
    char *destination = malloc(needed);
    char *temporary = malloc(needed + 16);
    if (destination == NULL || temporary == NULL) {
        free(destination);
        free(temporary);
        return false;
    }
    snprintf(destination, needed, "%s/phase-%020" PRIu64 ".khstate", directory, matching->phase);
    snprintf(temporary, needed + 16, "%s/.phase-XXXXXX", directory);
    int descriptor = mkstemp(temporary);
    FILE *file = descriptor < 0 ? NULL : fdopen(descriptor, "wb");
    if (file == NULL) {
        if (descriptor >= 0) {
            close(descriptor);
        }
        free(destination);
        free(temporary);
        return false;
    }
    hashed_writer_t writer = {.file = file, .valid = true};
    kh_sha256_init(&writer.checksum);
    hashed_write(&writer, "KHS1", 4);
    hashed_write(&writer, dp->digest, 32);
    hashed_u32(&writer, dp->p);
    hashed_u32(&writer, dp->r);
    hashed_u32(&writer, dp->q);
    hashed_u32(&writer, dp->count);
    hashed_u32(&writer, dp->f);
    hashed_u64(&writer, matching->phase);
    hashed_u32(&writer, matching->matched);
    for (uint32_t index = 0; index <= dp->r; ++index) {
        hashed_u32(&writer, polynomial[index]);
    }
    for (uint32_t left = 0; left < dp->count; ++left) {
        hashed_u32(&writer, matching->left[left]);
        hashed_u32(&writer, matching->choice[left]);
    }
    unsigned char digest[32];
    kh_sha256_final(&writer.checksum, digest);
    bool valid = writer.valid && fwrite(digest, 1, 32, file) == 32 &&
                 fflush(file) == 0 && fsync(fileno(file)) == 0;
    valid = fclose(file) == 0 && valid;
    valid = valid && link(temporary, destination) == 0;
    unlink(temporary);
    free(temporary);
    if (!valid) {
        free(destination);
        return false;
    }
    *saved_path = destination;
    return true;
}

static bool checkpoint_read_exact(FILE *file, kh_sha256_t *hash, void *output, size_t count) {
    if (fread(output, 1, count, file) != count) {
        return false;
    }
    kh_sha256_update(hash, output, count);
    return true;
}

static bool checkpoint_read_u32(FILE *file, kh_sha256_t *hash, uint32_t *output) {
    unsigned char bytes[4];
    if (!checkpoint_read_exact(file, hash, bytes, sizeof bytes)) {
        return false;
    }
    *output = load_u32(bytes);
    return true;
}

static bool checkpoint_read_u64(FILE *file, kh_sha256_t *hash, uint64_t *output) {
    unsigned char bytes[8];
    if (!checkpoint_read_exact(file, hash, bytes, sizeof bytes)) {
        return false;
    }
    *output = load_u64(bytes);
    return true;
}

static bool checkpoint_read(const char *path, const dp_t *dp,
                            const uint32_t *polynomial, matching_t *matching) {
    struct stat status;
    uint64_t expected = 68 + 4 * (uint64_t)(dp->r + 1) + 8 * (uint64_t)dp->count + 32;
    if (stat(path, &status) != 0 || status.st_size < 0 || (uint64_t)status.st_size != expected) {
        return false;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return false;
    }
    kh_sha256_t hash;
    kh_sha256_init(&hash);
    unsigned char magic[4];
    unsigned char digest[32];
    uint32_t p;
    uint32_t r;
    uint32_t q;
    uint32_t n;
    uint32_t f;
    uint32_t matched;
    bool valid = checkpoint_read_exact(file, &hash, magic, 4) &&
                 checkpoint_read_exact(file, &hash, digest, 32) &&
                 checkpoint_read_u32(file, &hash, &p) &&
                 checkpoint_read_u32(file, &hash, &r) &&
                 checkpoint_read_u32(file, &hash, &q) &&
                 checkpoint_read_u32(file, &hash, &n) &&
                 checkpoint_read_u32(file, &hash, &f) &&
                 checkpoint_read_u64(file, &hash, &matching->phase) &&
                 checkpoint_read_u32(file, &hash, &matched) &&
                 memcmp(magic, "KHS1", 4) == 0 && memcmp(digest, dp->digest, 32) == 0 &&
                 p == dp->p && r == dp->r && q == dp->q && n == dp->count && f == dp->f &&
                 matching->phase > 0 && matched <= dp->count;
    for (uint32_t index = 0; valid && index <= dp->r; ++index) {
        uint32_t coefficient;
        valid = checkpoint_read_u32(file, &hash, &coefficient) && coefficient == polynomial[index];
    }
    memset(matching->right, 255, (size_t)dp->q * sizeof(uint32_t));
    uint32_t counted = 0;
    for (uint32_t left = 0; valid && left < dp->count; ++left) {
        uint32_t right;
        uint32_t choice;
        valid = checkpoint_read_u32(file, &hash, &right) &&
                checkpoint_read_u32(file, &hash, &choice);
        if (!valid) {
            break;
        }
        if (right == ABSENT) {
            valid = choice == ABSENT;
        } else {
            valid = right < dp->q && choice < dp->f && matching->right[right] == ABSENT;
            if (valid) {
                matching->right[right] = left;
                ++counted;
            }
        }
        matching->left[left] = right;
        matching->choice[left] = choice;
    }
    unsigned char expected_digest[32];
    unsigned char actual_digest[32];
    kh_sha256_final(&hash, expected_digest);
    valid = valid && counted == matched &&
            fread(actual_digest, 1, 32, file) == 32 &&
            memcmp(actual_digest, expected_digest, 32) == 0 && fgetc(file) == EOF;
    valid = fclose(file) == 0 && valid;
    if (valid) {
        matching->matched = matched;
    }
    return valid;
}

static bool __attribute__((unused)) validate_matching(worker_t *workers, uint32_t worker_count, const dp_t *dp,
                              const matching_t *matching) {
    uint64_t batch = SCAN_TARGET_BYTES / (4 * (uint64_t)dp->f);
    if (batch == 0) {
        batch = 1;
    }
    uint32_t *lists[MAX_WORKERS] = {0};
    uint32_t counts[MAX_WORKERS] = {0};
    uint32_t positions[MAX_WORKERS] = {0};
    for (uint32_t left = 0; left < dp->count; ++left) {
        if (matching->left[left] != ABSENT) {
            ++counts[left % worker_count];
        }
    }
    bool valid = true;
    for (uint32_t index = 0; index < worker_count; ++index) {
        lists[index] = malloc((size_t)counts[index] * sizeof(uint32_t));
        valid = valid && (counts[index] == 0 || lists[index] != NULL);
    }
    uint32_t fill[MAX_WORKERS] = {0};
    for (uint32_t left = 0; valid && left < dp->count; ++left) {
        if (matching->left[left] != ABSENT) {
            uint32_t owner = left % worker_count;
            lists[owner][fill[owner]++] = left;
        }
    }
    while (valid) {
        scan_task_t tasks[MAX_WORKERS] = {0};
        bool any = false;
        for (uint32_t index = 0; index < worker_count; ++index) {
            uint32_t remaining = counts[index] - positions[index];
            uint32_t take = remaining < batch ? remaining : (uint32_t)batch;
            tasks[index] = (scan_task_t){&workers[index], lists[index] + positions[index],
                                         take, dp->f, NULL, false};
            if (take != 0) {
                tasks[index].labels = malloc((size_t)take * dp->f * sizeof(uint32_t));
                if (tasks[index].labels == NULL) {
                    valid = false;
                    break;
                }
                positions[index] += take;
                any = true;
            }
        }
        if (valid && !any) {
            for (uint32_t index = 0; index < worker_count; ++index) {
                free(tasks[index].labels);
            }
            break;
        }
        valid = valid && scan_parallel(tasks, worker_count);
        for (uint32_t index = 0; valid && index < worker_count; ++index) {
            for (uint64_t item = 0; item < tasks[index].count; ++item) {
                uint32_t left = tasks[index].left[item];
                uint32_t choice = matching->choice[left];
                if (choice >= dp->f || tasks[index].labels[item * dp->f + choice] != matching->left[left]) {
                    valid = false;
                    break;
                }
            }
        }
        for (uint32_t index = 0; index < worker_count; ++index) {
            free(tasks[index].labels);
        }
    }
    for (uint32_t index = 0; index < worker_count; ++index) {
        free(lists[index]);
    }
    return valid;
}

static bool writer_varint(hashed_writer_t *writer, uint64_t value) {
    unsigned char bytes[10];
    size_t count = 0;
    do {
        unsigned char byte = (unsigned char)(value & 127);
        value >>= 7;
        bytes[count++] = value == 0 ? byte : (unsigned char)(byte | 128);
    } while (value != 0);
    return hashed_write(writer, bytes, count);
}

static bool packed_write(hashed_writer_t *writer, const dp_t *dp,
                         const matching_t *matching, bool hall) {
    bool obstructed = matching->matched < dp->count;
    uint32_t maximum = hall ? 1 : dp->f - 1 + (uint32_t)obstructed;
    uint32_t bits = 0;
    while (maximum != 0) {
        ++bits;
        maximum >>= 1;
    }
    uint64_t accumulator = 0;
    uint32_t available = 0;
    for (uint32_t left = 0; left < dp->count; ++left) {
        uint32_t value;
        if (hall) {
            value = matching->distance[left] != ABSENT;
        } else {
            value = matching->choice[left] == ABSENT ? 0 :
                    matching->choice[left] + (uint32_t)obstructed;
        }
        accumulator |= (uint64_t)value << available;
        available += bits;
        while (available >= 8) {
            unsigned char byte = (unsigned char)accumulator;
            if (!hashed_write(writer, &byte, 1)) {
                return false;
            }
            accumulator >>= 8;
            available -= 8;
        }
    }
    if (available != 0) {
        unsigned char byte = (unsigned char)accumulator;
        return hashed_write(writer, &byte, 1);
    }
    return true;
}

static bool artifact_write(const char *path, const dp_t *dp, const uint32_t *polynomial,
                           const matching_t *matching) {
    size_t size = strlen(path) + 24;
    char *temporary = malloc(size);
    if (temporary == NULL) {
        return false;
    }
    snprintf(temporary, size, "%s.tmp.XXXXXX", path);
    int descriptor = mkstemp(temporary);
    FILE *file = descriptor < 0 ? NULL : fdopen(descriptor, "wb");
    if (file == NULL) {
        if (descriptor >= 0) {
            close(descriptor);
        }
        free(temporary);
        return false;
    }
    bool obstructed = matching->matched < dp->count;
    hashed_writer_t writer = {.file = file, .valid = true};
    kh_sha256_init(&writer.checksum);
    hashed_write(&writer, "KHM1", 4);
    hashed_write(&writer, dp->digest, 32);
    writer_varint(&writer, dp->p);
    writer_varint(&writer, dp->r);
    for (uint32_t index = 0; index <= dp->r; ++index) {
        writer_varint(&writer, polynomial[index]);
    }
    writer_varint(&writer, obstructed);
    writer_varint(&writer, dp->count);
    writer_varint(&writer, matching->matched);
    packed_write(&writer, dp, matching, false);
    if (obstructed) {
        packed_write(&writer, dp, matching, true);
    }
    unsigned char digest[32];
    kh_sha256_final(&writer.checksum, digest);
    bool valid = writer.valid && fwrite(digest, 1, 32, file) == 32 &&
                 fflush(file) == 0 && fsync(fileno(file)) == 0;
    valid = fclose(file) == 0 && valid;
    valid = valid && link(temporary, path) == 0;
    unlink(temporary);
    free(temporary);
    return valid;
}

static bool worker_fd(const char *text, worker_t *worker, uint32_t index) {
    char *copy = strdup(text);
    if (copy == NULL) {
        return false;
    }
    char *comma = strchr(copy, ',');
    if (comma == NULL) {
        free(copy);
        return false;
    }
    *comma = '\0';
    uint64_t read_descriptor;
    uint64_t write_descriptor;
    bool valid = parse_u64(copy, &read_descriptor) &&
                 parse_u64(comma + 1, &write_descriptor) &&
                 read_descriptor <= INT_MAX && write_descriptor <= INT_MAX &&
                 fcntl((int)read_descriptor, F_GETFD) >= 0 &&
                 fcntl((int)write_descriptor, F_GETFD) >= 0;
    free(copy);
    if (valid) {
        *worker = (worker_t){(int)read_descriptor, (int)write_descriptor, index};
    }
    return valid;
}

static void workers_stop(worker_t *workers, uint32_t count) {
    for (uint32_t index = 0; index < count; ++index) {
        kh_wire_write_request(workers[index].write_descriptor, KH_WIRE_STOP, 0);
    }
    for (uint32_t index = 0; index < count; ++index) {
        uint32_t status;
        uint64_t values;
        if (kh_wire_read_response(workers[index].read_descriptor, &status, &values) &&
            status == 0 && values == 2) {
            uint64_t cpu_microseconds;
            uint64_t peak_rss_bytes;
            if (kh_wire_read_u64(workers[index].read_descriptor, &cpu_microseconds) &&
                kh_wire_read_u64(workers[index].read_descriptor, &peak_rss_bytes)) {
                printf("{\"event\":\"resource_usage\",\"component\":\"shard\","
                       "\"shard_index\":%u,\"cpu_microseconds\":%" PRIu64
                       ",\"peak_rss_bytes\":%" PRIu64 "}\n",
                       index, cpu_microseconds, peak_rss_bytes);
                fflush(stdout);
            }
        }
    }
}

static void help(void) {
    puts("Native distributed exact matching coordinator.\n"
         "Usage: kh_match_distributed INPUT.khdp OUTPUT.khmatch --poly C0,...,Cr\n"
         "       --worker-fd READ,WRITE --worker-fd READ,WRITE [options]\n"
         "Options: --threads-per-worker N --max-bytes N --max-edges N\n"
         "         --max-field-elements N\n"
         "         --checkpoint-dir PATH --resume PATH --stop-after-phases N\n"
         "         --checkpoint-handshake");
}

int main(int argc, char **argv) {
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }
    if (argc < 3) {
        help();
        return 1;
    }
    const char *dp_path = argv[1];
    const char *output = argv[2];
    const char *poly_text = NULL;
    const char *checkpoint_directory = NULL;
    const char *resume = NULL;
    uint64_t threads = 1;
    uint64_t max_bytes = UINT64_C(2147483648);
    uint64_t max_edges = UINT64_MAX;
    uint64_t max_field_elements = UINT32_MAX;
    uint64_t stop_after = 0;
    bool handshake = false;
    worker_t workers[MAX_WORKERS];
    uint32_t worker_count = 0;
    bool arguments_valid = true;
    for (int index = 3; arguments_valid && index < argc; ++index) {
        const char *option = argv[index];
        if (!strcmp(option, "--checkpoint-handshake")) {
            handshake = true;
            continue;
        }
        if (++index >= argc) {
            arguments_valid = false;
            break;
        }
        const char *value = argv[index];
        if (!strcmp(option, "--poly")) {
            poly_text = value;
        } else if (!strcmp(option, "--checkpoint-dir")) {
            checkpoint_directory = value;
        } else if (!strcmp(option, "--resume")) {
            resume = value;
        } else if (!strcmp(option, "--worker-fd")) {
            arguments_valid = worker_count < MAX_WORKERS &&
                              worker_fd(value, &workers[worker_count], worker_count);
            if (arguments_valid) {
                ++worker_count;
            }
        } else {
            uint64_t number = 0;
            arguments_valid = parse_u64(value, &number);
            if (!strcmp(option, "--threads-per-worker")) {
                threads = number;
            } else if (!strcmp(option, "--max-bytes")) {
                max_bytes = number;
            } else if (!strcmp(option, "--max-edges")) {
                max_edges = number;
            } else if (!strcmp(option, "--max-field-elements")) {
                max_field_elements = number;
            } else if (!strcmp(option, "--stop-after-phases")) {
                stop_after = number;
            } else {
                arguments_valid = false;
            }
        }
    }
    dp_t dp = {0};
    uint32_t polynomial[32] = {0};
    matching_t matching = {0};
    uint64_t required = 0;
    const char *error = NULL;
    bool initialized = false;
    bool success = arguments_valid && worker_count >= 2 && poly_text != NULL &&
                   threads > 0 && threads <= 1024 && max_bytes > 0 && max_edges > 0 &&
                   max_field_elements > 0 &&
                   (!handshake || checkpoint_directory != NULL) &&
                   (!stop_after || checkpoint_directory != NULL) &&
                   dp_read(dp_path, &dp) && polynomial_read(poly_text, &dp, polynomial) &&
                   dp.q <= max_field_elements &&
                   matching_create(&dp, max_bytes, &matching, &required);
    if (!success) {
        error = "invalid input, worker descriptors, or coordinator memory admission";
        goto cleanup;
    }
    initialized = workers_initialize(workers, worker_count, &dp, polynomial,
                                     (uint32_t)threads, max_bytes);
    if (!initialized) {
        error = "native matching worker initialization failed";
        success = false;
        goto cleanup;
    }
    if (resume != NULL) {
        if (!checkpoint_read(resume, &dp, polynomial, &matching) ||
            !workers_restore(workers, worker_count, &dp, &matching)) {
            error = "distributed checkpoint validation failed";
            success = false;
            goto cleanup;
        }
        printf("{\"event\":\"restored\",\"done\":%u,\"total\":%u,"
               "\"checkpoint_done\":%u,\"phase\":\"restored\","
               "\"units\":\"requests\",\"heartbeat\":true}\n",
               matching.matched, dp.count, matching.matched);
        fflush(stdout);
    }
    bool obstructed = false;
    uint32_t hall_left = 0;
    uint32_t hall_right = 0;
    while (matching.matched < dp.count) {
        uint32_t shortest;
        if (!reduced_breadth_first(workers, worker_count, &dp, &matching,
                                   max_edges, &shortest, &error)) {
            success = false;
            goto cleanup;
        }
        if (shortest == ABSENT) {
            obstructed = true;
            if (!reduced_hall(workers, worker_count, &dp, &matching,
                              &hall_left, &hall_right, &error)) {
                success = false;
                goto cleanup;
            }
            break;
        }
        uint32_t paths;
        uint32_t previous = matching.matched;
        if (!reduced_augment(workers, worker_count, &dp, &matching,
                             shortest, &paths, &error) || paths == 0) {
            if (error == NULL) {
                error = "native shortest-path layer made no progress";
            }
            success = false;
            goto cleanup;
        }
        ++matching.phase;
        char *saved = NULL;
        if (checkpoint_directory != NULL &&
            !checkpoint_write(checkpoint_directory, &dp, polynomial, &matching, &saved)) {
            error = "cannot publish native distributed checkpoint";
            success = false;
            goto cleanup;
        }
        printf("{\"event\":\"committed\",\"cursor\":%" PRIu64
               ",\"augmented\":%u,\"matched\":%u,\"done\":%u,\"total\":%u,"
               "\"checkpoint_done\":%u,\"phase\":\"matching\","
               "\"units\":\"requests\",\"heartbeat\":true",
               matching.phase, paths, matching.matched, matching.matched, dp.count,
               handshake ? previous : matching.matched);
        if (saved != NULL) {
            printf(",\"checkpoint\":\"%s\"", saved);
        }
        puts("}");
        fflush(stdout);
        free(saved);
        if (handshake) {
            printf("{\"event\":\"checkpoint\",\"cursor\":%" PRIu64 "}\n", matching.phase);
            fflush(stdout);
            if (getchar() != '\n') {
                error = "cluster checkpoint acknowledgment missing";
                success = false;
                goto cleanup;
            }
            printf("{\"done\":%u,\"total\":%u,\"checkpoint_done\":%u,"
                   "\"phase\":\"matching\",\"units\":\"requests\","
                   "\"heartbeat\":true}\n",
                   matching.matched, dp.count, matching.matched);
            fflush(stdout);
        }
        if (stop_after != 0 && matching.phase >= stop_after) {
            workers_stop(workers, worker_count);
            matching_free(&matching);
            dp_free(&dp);
            kh_resource_print("coordinator", -1);
            return 3;
        }
    }
    if (!obstructed && matching.matched != dp.count) {
        error = "native matching terminated without a result";
        success = false;
        goto cleanup;
    }
    if (!artifact_write(output, &dp, polynomial, &matching)) {
        error = "cannot publish native distributed certificate";
        success = false;
        goto cleanup;
    }
    printf("{\"p\":%u,\"r\":%u,\"polynomial\":[", dp.p, dp.r);
    for (uint32_t index = 0; index <= dp.r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    printf("],\"status\":%u,\"required\":%u,\"matched\":%u,"
           "\"phases\":%" PRIu64 ",\"scans\":%" PRIu64
           ",\"memory_required\":%" PRIu64 ",\"hall_left\":%u,"
           "\"hall_right\":%u}\n",
           obstructed, dp.count, matching.matched, matching.phase, matching.scans,
           required, hall_left, hall_right);
    printf("{\"done\":%u,\"total\":%u,\"checkpoint_done\":%u,"
           "\"phase\":\"complete\",\"units\":\"requests\","
           "\"message\":\"%s\"}\n",
           matching.matched, dp.count, matching.matched,
           obstructed ? "hall_obstruction" : "full_matching");
    fflush(stdout);

cleanup:
    if (initialized) {
        workers_stop(workers, worker_count);
    }
    if (!success && error != NULL) {
        fprintf(stderr, "kh_match_distributed: %s\n", error);
    }
    matching_free(&matching);
    dp_free(&dp);
    kh_resource_print("coordinator", -1);
    return success ? 0 : 1;
}
