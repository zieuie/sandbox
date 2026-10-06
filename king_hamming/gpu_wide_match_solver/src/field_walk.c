#define _GNU_SOURCE

#include "field_walk.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

// Elements are base-p digit vectors d[0..r-1] (low degree first), so 64-bit fields need no
// packed arithmetic: multiplying by X shifts the digits and folds the carried top digit back
// with a table of (p - carry * c_i mod p), with no divisions in the inner loop.

typedef struct {
    uint32_t d[32];
} element_t;

typedef struct {
    uint32_t p, r, half;
    uint32_t poly[32];
} ring_t;

static ring_t ring_of(const kh_parameters_t *parameters, const uint16_t *polynomial) {
    ring_t ring = {parameters->p, parameters->r, parameters->r / 2, {0}};
    for (uint32_t index = 0; index <= ring.r; ++index) {
        ring.poly[index] = polynomial[index];
    }
    return ring;
}

static element_t multiply(const ring_t *ring, const element_t *a, const element_t *b) {
    uint64_t product[64] = {0};
    uint32_t p = ring->p, r = ring->r;
    for (uint32_t i = 0; i < r; ++i) {
        for (uint32_t j = 0; j < r; ++j) {
            product[i + j] = (product[i + j] + (uint64_t)a->d[i] * b->d[j]) % p;
        }
    }
    // X^r = -(c_0 + c_1 X + ... + c_{r-1} X^{r-1}) for the monic polynomial.
    for (uint32_t degree = 2 * r - 2; degree >= r; --degree) {
        uint64_t factor = product[degree];
        for (uint32_t index = 0; index < r; ++index) {
            uint32_t offset = degree - r + index;
            product[offset] = (product[offset] + p - factor * ring->poly[index] % p) % p;
        }
        product[degree] = 0;
    }
    element_t result = {{0}};
    for (uint32_t index = 0; index < r; ++index) {
        result.d[index] = (uint32_t)product[index];
    }
    return result;
}

static element_t power_x(const ring_t *ring, uint64_t exponent) {
    element_t result = {{0}}, base = {{0}};
    result.d[0] = 1;
    base.d[1] = 1;  // r >= 3, so X is a plain digit vector
    while (exponent != 0) {
        if (exponent & 1) {
            result = multiply(ring, &result, &base);
        }
        exponent >>= 1;
        if (exponent != 0) {
            base = multiply(ring, &base, &base);
        }
    }
    return result;
}

static bool is_one(const ring_t *ring, const element_t *element) {
    for (uint32_t index = 0; index < ring->r; ++index) {
        if (element->d[index] != (index == 0)) {
            return false;
        }
    }
    return true;
}

bool fw_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial) {
    uint32_t p = parameters->p, r = parameters->r;
    if (parameters->q < 2 || parameters->q >= FW_MAX_Q || r < 3 || r > 31 || polynomial[r] != 1 || polynomial[0] == 0) {
        return false;
    }
    for (uint32_t index = 0; index <= r; ++index) {
        if (polynomial[index] >= p) {
            return false;
        }
    }
    ring_t ring = ring_of(parameters, polynomial);
    uint64_t order = parameters->q - 1;
    element_t value = power_x(&ring, order);
    if (!is_one(&ring, &value)) {
        return false;
    }
    // X has order exactly q-1 when no maximal proper divisor of q-1 already gives 1.
    uint64_t remaining = order;
    for (uint64_t divisor = 2; divisor <= remaining / divisor; ++divisor) {
        if (remaining % divisor != 0) {
            continue;
        }
        value = power_x(&ring, order / divisor);
        if (is_one(&ring, &value)) {
            return false;
        }
        do {
            remaining /= divisor;
        } while (remaining % divisor == 0);
    }
    if (remaining > 1) {
        value = power_x(&ring, order / remaining);
        if (is_one(&ring, &value)) {
            return false;
        }
    }
    return true;
}

bool fw_generate(const kh_parameters_t *parameters, uint64_t start, uint16_t *polynomial, uint64_t *chosen) {
    if (parameters->q >= FW_MAX_Q) {
        return false;
    }
    for (uint64_t candidate = start; candidate < parameters->q; ++candidate) {
        if (candidate % parameters->p == 0) {
            continue;
        }
        uint64_t packed = candidate;
        for (uint32_t index = 0; index < parameters->r; ++index) {
            polynomial[index] = (uint16_t)(packed % parameters->p);
            packed /= parameters->p;
        }
        polynomial[parameters->r] = 1;
        if (fw_primitive(parameters, polynomial)) {
            *chosen = candidate;
            return true;
        }
    }
    return false;
}


// ----- walks ---------------------------------------------------------------------------------

struct fw_walk {
    kh_parameters_t parameters;
    ring_t ring;
    uint32_t *fold;          // fold[carry * r + i] = (p - carry * c_i mod p) mod p
    uint64_t power[32];      // p^i for the suffix digits
    uint64_t *starts;        // chunk c covers labels [starts[c], starts[c + 1]); starts[chunks] = q
    uint32_t chunks, threads, shift, nbp;
    uint32_t *offsets;       // chunks * budget: chunk c's first row position in each cell
    bool cycle_ok;
};

typedef struct {
    fw_walk_t *walk;
    const uint32_t *slot;    // placement: cell -> slot (NULL while counting)
    uint32_t *pos;           // placement: chunks * nslots running positions
    uint32_t *rows;
    uint64_t nslots;
    uint32_t mask;
    uint64_t processed;      // labels visited (atomic, approximate)
    uint32_t finished;       // workers done (atomic)
    uint32_t next_chunk;     // work queue (atomic)
} job_t;

#define PROGRESS_STRIDE (UINT64_C(1) << 22)

static inline uint64_t cell_of(const fw_walk_t *walk, const element_t *element) {
    const ring_t *ring = &walk->ring;
    uint64_t suffix = 0, prefix = 0;
    for (uint32_t index = 0; index < ring->half; ++index) {
        suffix += element->d[index] * walk->power[index];
    }
    for (uint32_t index = ring->half; index < ring->r; ++index) {
        prefix += element->d[index];
    }
    return (prefix % ring->p) * walk->parameters.f + suffix;
}

static inline void times_x(const fw_walk_t *walk, element_t *element) {
    const ring_t *ring = &walk->ring;
    uint32_t r = ring->r, p = ring->p;
    uint32_t carry = element->d[r - 1];
    for (uint32_t index = r - 1; index > 0; --index) {
        element->d[index] = element->d[index - 1];
    }
    element->d[0] = 0;
    if (carry != 0) {
        const uint32_t *fold = walk->fold + (uint64_t)carry * r;
        for (uint32_t index = 0; index < r; ++index) {
            uint32_t digit = element->d[index] + fold[index];
            element->d[index] = digit >= p ? digit - p : digit;
        }
    }
}

// Chunks are taken from a shared counter, so a slow thread doesn't hold up the walk.
static void *run_chunks(void *raw) {
    job_t *job = raw;
    fw_walk_t *walk = job->walk;
    uint64_t budget = walk->parameters.budget, f = walk->parameters.f, q = walk->parameters.q;
    for (;;) {
        uint32_t chunk = __atomic_fetch_add(&job->next_chunk, 1, __ATOMIC_RELAXED);
        if (chunk >= walk->chunks) break;
        uint64_t first = walk->starts[chunk], last = walk->starts[chunk + 1];
        element_t element = power_x(&walk->ring, first - 1);   // label z is X^(z-1)
        if (job->slot == NULL) {
            uint32_t *counts = walk->offsets + (uint64_t)chunk * budget;
            for (uint64_t label = first; label < last; ++label) {
                ++counts[cell_of(walk, &element)];
                times_x(walk, &element);
                if ((label & (PROGRESS_STRIDE - 1)) == 0) {
                    __atomic_fetch_add(&job->processed, PROGRESS_STRIDE, __ATOMIC_RELAXED);
                }
            }
            if (last == q && !is_one(&walk->ring, &element)) {
                walk->cycle_ok = false;
            }
        } else {
            uint32_t *pos = job->pos + (uint64_t)chunk * job->nslots;
            for (uint64_t label = first; label < last; ++label) {
                uint32_t slot = job->slot[cell_of(walk, &element)];
                if (slot != FW_NO_SLOT) {
                    job->rows[(uint64_t)slot * f + pos[slot]++] = (uint32_t)(label & job->mask);
                }
                times_x(walk, &element);
                if ((label & (PROGRESS_STRIDE - 1)) == 0) {
                    __atomic_fetch_add(&job->processed, PROGRESS_STRIDE, __ATOMIC_RELAXED);
                }
            }
        }
    }
    __atomic_fetch_add(&job->finished, 1, __ATOMIC_RELEASE);
    return NULL;
}

static double seconds_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static bool run_walk(job_t *job, fw_progress_t progress, void *context) {
    fw_walk_t *walk = job->walk;
    pthread_t *threads = calloc(walk->threads, sizeof *threads);
    bool ok = threads != NULL;
    uint32_t started = 0;
    for (; ok && started < walk->threads; ++started) {
        if (pthread_create(&threads[started], NULL, run_chunks, job) != 0) {
            ok = false;
            break;
        }
    }
    uint64_t total = walk->parameters.q - 1;
    double last = seconds_now();
    while (progress != NULL && __atomic_load_n(&job->finished, __ATOMIC_ACQUIRE) < started) {
        struct timespec pause = {0, 200000000};
        nanosleep(&pause, NULL);
        if (seconds_now() - last >= 2.0) {
            uint64_t done = __atomic_load_n(&job->processed, __ATOMIC_RELAXED);
            progress(done < total ? done : total, total, context);
            last = seconds_now();
        }
    }
    for (uint32_t index = 0; index < started; ++index) {
        pthread_join(threads[index], NULL);
    }
    if (progress != NULL && ok) {
        progress(total, total, context);
    }
    free(threads);
    return ok;
}

static int compare_u64(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left, b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

static uint32_t breakpoint_count(uint64_t q, uint32_t shift) {
    return (uint32_t)((q - 1) >> shift);   // high parts 0 .. (q-1) >> shift
}

// Two chunks per thread keep the work queue balanced; one more chunk starts at every multiple
// of 2^shift, so a breakpoint is simply that chunk's starting row position.
static uint64_t chunk_bound(uint32_t threads, uint32_t nbp) {
    return 2 * (uint64_t)threads + nbp + 1;
}

uint64_t fw_walk_bytes(const kh_parameters_t *parameters, uint32_t threads, uint32_t shift) {
    uint64_t chunks = chunk_bound(threads, breakpoint_count(parameters->q, shift));
    return 4 * chunks * parameters->budget + 8 * (chunks + 1) + (uint64_t)parameters->p * parameters->r * 4;
}

uint64_t fw_row_bytes(const kh_parameters_t *parameters, uint32_t shift) {
    return 4 * ((uint64_t)parameters->f + breakpoint_count(parameters->q, shift));
}

uint64_t fw_rows_bytes(const kh_parameters_t *parameters, uint32_t threads, uint32_t shift, uint64_t ncells) {
    uint64_t chunks = chunk_bound(threads, breakpoint_count(parameters->q, shift));
    return fw_row_bytes(parameters, shift) * ncells + 4 * (uint64_t)parameters->budget + 4 * chunks * ncells;
}

fw_walk_t *fw_walk_prepare(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                           uint32_t shift, fw_progress_t progress, void *context, const char **error) {
    *error = NULL;
    uint64_t q = parameters->q, f = parameters->f, budget = parameters->budget;
    if (q >= FW_MAX_Q || threads == 0 || threads > 1024 || shift == 0 || shift > 32 ||
        breakpoint_count(q, shift) > FW_MAX_BREAKPOINTS) {
        *error = "field too large for the wide builder, or invalid controls";
        return NULL;
    }
    if (!fw_primitive(parameters, polynomial)) {
        *error = "polynomial is not primitive with generator X";
        return NULL;
    }
    fw_walk_t *walk = calloc(1, sizeof *walk);
    uint32_t nbp = breakpoint_count(q, shift);
    uint64_t bound = chunk_bound(threads, nbp);
    if (walk == NULL || (walk->starts = malloc((bound + 1) * sizeof *walk->starts)) == NULL) {
        free(walk);
        *error = "host allocation failed";
        return NULL;
    }
    walk->parameters = *parameters;
    walk->ring = ring_of(parameters, polynomial);
    walk->threads = threads;
    walk->shift = shift;
    walk->nbp = nbp;
    walk->cycle_ok = true;
    for (uint32_t index = 0, value = 1; index < walk->ring.half; ++index, value *= parameters->p) {
        walk->power[index] = value;
    }
    uint32_t chunks = 0;
    for (uint32_t index = 0; index < 2 * threads; ++index) {
        walk->starts[chunks++] = 1 + (q - 1) * index / (2 * threads);   // q < 2^40, 2048 parts: no overflow
    }
    for (uint64_t high = 1; high <= nbp; ++high) {
        walk->starts[chunks++] = high << shift;
    }
    qsort(walk->starts, chunks, sizeof *walk->starts, compare_u64);
    uint32_t unique = 0;
    for (uint32_t index = 0; index < chunks; ++index) {
        if ((unique == 0 || walk->starts[unique - 1] != walk->starts[index]) && walk->starts[index] < q) {
            walk->starts[unique++] = walk->starts[index];
        }
    }
    walk->chunks = unique;
    walk->starts[unique] = q;
    walk->fold = malloc((uint64_t)parameters->p * parameters->r * sizeof *walk->fold);
    walk->offsets = calloc((uint64_t)walk->chunks * budget, sizeof *walk->offsets);
    if (walk->fold == NULL || walk->offsets == NULL) {
        fw_walk_free(walk);
        *error = "host allocation failed";
        return NULL;
    }
    for (uint32_t carry = 0; carry < parameters->p; ++carry) {
        for (uint32_t index = 0; index < parameters->r; ++index) {
            uint64_t product = (uint64_t)carry * walk->ring.poly[index] % parameters->p;
            walk->fold[carry * parameters->r + index] = (uint32_t)((parameters->p - product) % parameters->p);
        }
    }
    job_t job = {.walk = walk};
    if (!run_walk(&job, progress, context)) {
        fw_walk_free(walk);
        *error = "cannot start field threads";
        return NULL;
    }
    if (!walk->cycle_ok) {
        fw_walk_free(walk);
        *error = "generator cycle does not close";
        return NULL;
    }
    // Prefix sums: each chunk's labels follow the earlier chunks' labels in its row. Label 0
    // (the zero element) is in cell 0 and comes first.
    for (uint64_t cell = 0; cell < budget; ++cell) {
        uint64_t offset = cell == 0 ? 1 : 0;
        for (uint32_t chunk = 0; chunk < walk->chunks; ++chunk) {
            uint32_t *slot = &walk->offsets[(uint64_t)chunk * budget + cell];
            uint32_t count = *slot;
            *slot = (uint32_t)offset;
            offset += count;
        }
        if (offset != f) {
            fw_walk_free(walk);
            *error = "field partition cardinality invariant failed";
            return NULL;
        }
    }
    return walk;
}

bool fw_rows_alloc(fw_rows_t *rows, uint64_t budget, uint32_t f, uint32_t nbp, uint32_t shift, uint64_t ncells) {
    memset(rows, 0, sizeof *rows);
    rows->rows = malloc((ncells ? ncells : 1) * f * sizeof *rows->rows);
    rows->bp = nbp ? malloc((ncells ? ncells : 1) * nbp * sizeof *rows->bp) : NULL;
    rows->slot = malloc(budget * sizeof *rows->slot);
    if (rows->rows == NULL || rows->slot == NULL || (nbp && rows->bp == NULL)) {
        fw_free(rows);
        return false;
    }
    memset(rows->slot, 0xFF, budget * sizeof *rows->slot);   // FW_NO_SLOT
    rows->nslots = ncells;
    rows->budget = budget;
    rows->f = f;
    rows->nbp = nbp;
    rows->shift = shift;
    return true;
}

bool fw_walk_rows(fw_walk_t *walk, const uint64_t *cells, uint64_t ncells, fw_progress_t progress, void *context,
                  fw_rows_t *output, const char **error) {
    *error = NULL;
    const kh_parameters_t *parameters = &walk->parameters;
    uint64_t budget = parameters->budget, f = parameters->f, q = parameters->q;
    uint32_t nbp = walk->nbp, chunks = walk->chunks;
    if (ncells >= UINT32_MAX || !fw_rows_alloc(output, budget, (uint32_t)f, nbp, walk->shift, ncells)) {
        *error = "host allocation failed";
        return false;
    }
    for (uint64_t slot = 0; slot < ncells; ++slot) {
        if (cells[slot] >= budget || (slot && cells[slot] <= cells[slot - 1])) {
            fw_free(output);
            *error = "pass cells must ascend and lie below the budget";
            return false;
        }
        output->slot[cells[slot]] = (uint32_t)slot;
    }
    uint32_t *pos = malloc(((uint64_t)chunks * ncells + 1) * sizeof *pos);
    if (pos == NULL) {
        fw_free(output);
        *error = "host allocation failed";
        return false;
    }
    for (uint32_t chunk = 0; chunk < chunks; ++chunk) {
        for (uint64_t slot = 0; slot < ncells; ++slot) {
            pos[(uint64_t)chunk * ncells + slot] = walk->offsets[(uint64_t)chunk * budget + cells[slot]];
        }
        uint64_t start = walk->starts[chunk];
        if (nbp != 0 && start % (UINT64_C(1) << walk->shift) == 0) {
            for (uint64_t slot = 0; slot < ncells; ++slot) {
                output->bp[slot * nbp + (start >> walk->shift) - 1] = pos[(uint64_t)chunk * ncells + slot];
            }
        }
    }
    if (ncells != 0 && cells[0] == 0) {
        output->rows[0] = 0;   // label 0 heads cell 0's row
    }
    job_t job = {.walk = walk, .slot = output->slot, .pos = pos, .rows = output->rows, .nslots = ncells,
                 .mask = walk->shift == 32 ? UINT32_MAX : (UINT32_C(1) << walk->shift) - 1};
    bool ok = run_walk(&job, progress, context);
    // Every chunk must have placed exactly the labels it counted in every held row.
    for (uint32_t chunk = 0; ok && chunk < chunks; ++chunk) {
        for (uint64_t slot = 0; slot < ncells; ++slot) {
            uint32_t end = chunk + 1 < chunks ? walk->offsets[(uint64_t)(chunk + 1) * budget + cells[slot]] : (uint32_t)f;
            if (pos[(uint64_t)chunk * ncells + slot] != end) {
                ok = false;
                *error = "placement walk disagrees with the count walk";
                break;
            }
        }
    }
    free(pos);
    if (!ok) {
        if (*error == NULL) *error = "cannot start field threads";
        fw_free(output);
        return false;
    }
    // Spot-check: a label in a held cell must sit in that cell's row, found by binary search
    // (which also needs the row ascending there), computed independently with power_x.
    uint64_t state = UINT64_C(0x9E3779B97F4A7C15) ^ q ^ (ncells ? cells[0] : 0);
    for (uint32_t tries = 0, hits = 0; ncells != 0 && tries < 200000 && hits < 4000; ++tries) {
        state = state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
        uint64_t label = 1 + (state >> 11) % (q - 1);
        element_t element = power_x(&walk->ring, label - 1);
        uint64_t cell = cell_of(walk, &element);
        if (output->slot[cell] == FW_NO_SLOT) {
            continue;
        }
        ++hits;
        uint32_t lo = 0, hi = (uint32_t)f;
        while (lo < hi) {
            uint32_t mid = lo + (hi - lo) / 2;
            if (fw_label(output, cell, mid) < label) lo = mid + 1; else hi = mid;
        }
        if (lo == f || fw_label(output, cell, lo) != label) {
            fw_free(output);
            *error = "field rows failed the spot check";
            return false;
        }
    }
    return true;
}

void fw_walk_free(fw_walk_t *walk) {
    if (walk == NULL) return;
    free(walk->fold);
    free(walk->offsets);
    free(walk->starts);
    free(walk);
}

bool fw_build_rows(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                   uint64_t limit, uint32_t shift, fw_rows_t *output, const char **error) {
    fw_walk_t *walk = fw_walk_prepare(parameters, polynomial, threads, shift, NULL, NULL, error);
    if (walk == NULL) return false;
    uint64_t *cells = malloc((limit ? limit : 1) * sizeof *cells);
    bool ok = cells != NULL;
    for (uint64_t cell = 0; ok && cell < limit; ++cell) cells[cell] = cell;
    ok = ok ? fw_walk_rows(walk, cells, limit, NULL, NULL, output, error) : (*error = "host allocation failed", false);
    free(cells);
    fw_walk_free(walk);
    return ok;
}

void fw_free(fw_rows_t *rows) {
    free(rows->rows);
    free(rows->bp);
    free(rows->slot);
    memset(rows, 0, sizeof *rows);
}
