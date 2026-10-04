#define _GNU_SOURCE

#include "field_prefix.h"

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

bool fp_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial) {
    uint32_t p = parameters->p, r = parameters->r;
    if (parameters->q < 2 || parameters->q >= FP_MAX_Q || r < 3 || r > 31 || polynomial[r] != 1 || polynomial[0] == 0) {
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

bool fp_generate(const kh_parameters_t *parameters, uint64_t start, uint16_t *polynomial, uint64_t *chosen) {
    if (parameters->q >= FP_MAX_Q) {
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
        if (fp_primitive(parameters, polynomial)) {
            *chosen = candidate;
            return true;
        }
    }
    return false;
}

// ----- construction --------------------------------------------------------------------------

typedef struct {
    const kh_parameters_t *parameters;
    ring_t ring;
    uint32_t *fold;          // fold[carry * r + i] = (p - carry * c_i mod p) mod p
    uint64_t power[32];      // p^i for the suffix digits
    uint64_t *starts;        // chunk c covers labels [starts[c], starts[c + 1]); starts[chunks] = q
    uint32_t chunks, threads;
    uint32_t *counts;        // chunks * budget: counts, then each chunk's next row position
    uint32_t *rows;
    uint64_t limit;
    uint32_t mask;
    bool writing;
    bool cycle_ok;
    uint64_t processed;      // labels visited so far, both passes (atomic)
    uint32_t finished;       // workers done with this pass (atomic)
    fp_progress_t progress;
    void *context;
} build_t;

#define PROGRESS_STRIDE (UINT64_C(1) << 22)

typedef struct {
    build_t *build;
    uint32_t worker;
} worker_t;

static inline uint64_t cell_of(const build_t *build, const element_t *element) {
    const ring_t *ring = &build->ring;
    uint64_t suffix = 0, prefix = 0;
    for (uint32_t index = 0; index < ring->half; ++index) {
        suffix += element->d[index] * build->power[index];
    }
    for (uint32_t index = ring->half; index < ring->r; ++index) {
        prefix += element->d[index];
    }
    return (prefix % ring->p) * build->parameters->f + suffix;
}

static inline void times_x(const build_t *build, element_t *element) {
    const ring_t *ring = &build->ring;
    uint32_t r = ring->r, p = ring->p;
    uint32_t carry = element->d[r - 1];
    for (uint32_t index = r - 1; index > 0; --index) {
        element->d[index] = element->d[index - 1];
    }
    element->d[0] = 0;
    if (carry != 0) {
        const uint32_t *fold = build->fold + (uint64_t)carry * r;
        for (uint32_t index = 0; index < r; ++index) {
            uint32_t digit = element->d[index] + fold[index];
            element->d[index] = digit >= p ? digit - p : digit;
        }
    }
}

static void *run_chunks(void *raw) {
    worker_t *worker = raw;
    build_t *build = worker->build;
    uint64_t budget = build->parameters->budget, f = build->parameters->f;
    for (uint32_t chunk = worker->worker; chunk < build->chunks; chunk += build->threads) {
        uint64_t first = build->starts[chunk], last = build->starts[chunk + 1];
        uint32_t *counts = build->counts + (uint64_t)chunk * budget;
        element_t element = power_x(&build->ring, first - 1);   // label z is X^(z-1)
        if (!build->writing) {
            for (uint64_t label = first; label < last; ++label) {
                ++counts[cell_of(build, &element)];
                times_x(build, &element);
                if ((label & (PROGRESS_STRIDE - 1)) == 0) {
                    __atomic_fetch_add(&build->processed, PROGRESS_STRIDE, __ATOMIC_RELAXED);
                }
            }
            if (last == build->parameters->q && !is_one(&build->ring, &element)) {
                build->cycle_ok = false;
            }
        } else {
            for (uint64_t label = first; label < last; ++label) {
                uint64_t cell = cell_of(build, &element);
                if (cell < build->limit) {
                    build->rows[cell * f + counts[cell]++] = (uint32_t)(label & build->mask);
                }
                times_x(build, &element);
                if ((label & (PROGRESS_STRIDE - 1)) == 0) {
                    __atomic_fetch_add(&build->processed, PROGRESS_STRIDE, __ATOMIC_RELAXED);
                }
            }
        }
    }
    __atomic_fetch_add(&build->finished, 1, __ATOMIC_RELEASE);
    return NULL;
}

static double seconds_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static bool run_pass(build_t *build) {
    pthread_t *threads = calloc(build->threads, sizeof *threads);
    worker_t *workers = calloc(build->threads, sizeof *workers);
    bool ok = threads != NULL && workers != NULL;
    uint32_t started = 0;
    __atomic_store_n(&build->finished, 0, __ATOMIC_RELAXED);
    for (; ok && started < build->threads; ++started) {
        workers[started] = (worker_t){build, started};
        if (pthread_create(&threads[started], NULL, run_chunks, &workers[started]) != 0) {
            ok = false;
            break;
        }
    }
    // Report every few seconds while the workers run (the stride makes the count approximate).
    uint64_t total = 2 * (build->parameters->q - 1), base = build->writing ? build->parameters->q - 1 : 0;
    double last = seconds_now();
    while (build->progress != NULL && __atomic_load_n(&build->finished, __ATOMIC_ACQUIRE) < started) {
        struct timespec pause = {0, 200000000};
        nanosleep(&pause, NULL);
        if (seconds_now() - last >= 2.0) {
            uint64_t done = __atomic_load_n(&build->processed, __ATOMIC_RELAXED);
            build->progress(done < total ? done : total, total, build->context);
            last = seconds_now();
        }
    }
    for (uint32_t index = 0; index < started; ++index) {
        pthread_join(threads[index], NULL);
    }
    __atomic_store_n(&build->processed, base + build->parameters->q - 1, __ATOMIC_RELAXED);
    if (build->progress != NULL && ok) {
        build->progress(build->processed, total, build->context);
    }
    free(threads);
    free(workers);
    return ok;
}

static int compare_u64(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left, b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

static uint32_t breakpoint_count(uint64_t q, uint32_t shift) {
    return (uint32_t)((q - 1) >> shift);   // high parts 0 .. (q-1) >> shift
}

uint64_t fp_build_bytes(const kh_parameters_t *parameters, uint32_t threads, uint64_t limit, uint32_t shift) {
    uint64_t nbp = breakpoint_count(parameters->q, shift);
    uint64_t chunks = (uint64_t)threads + nbp + 1;
    return 4 * limit * parameters->f + 4 * limit * nbp + 4 * chunks * parameters->budget +
           (uint64_t)parameters->p * parameters->r * 4;
}

bool fp_build_rows(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads,
                   uint64_t limit, uint32_t shift, fp_progress_t progress, void *context,
                   fp_rows_t *output, const char **error) {
    memset(output, 0, sizeof *output);
    *error = NULL;
    uint64_t q = parameters->q, f = parameters->f, budget = parameters->budget;
    if (q >= FP_MAX_Q || threads == 0 || threads > 1024 || shift == 0 || shift > 32 || limit == 0 ||
        limit > budget || breakpoint_count(q, shift) > FP_MAX_BREAKPOINTS) {
        *error = "field too large for prefix construction, or invalid controls";
        return false;
    }
    if (!fp_primitive(parameters, polynomial)) {
        *error = "polynomial is not primitive with generator X";
        return false;
    }
    uint32_t nbp = breakpoint_count(q, shift);
    build_t build = {0};
    build.parameters = parameters;
    build.ring = ring_of(parameters, polynomial);
    build.threads = threads;
    build.limit = limit;
    build.mask = shift == 32 ? UINT32_MAX : (UINT32_C(1) << shift) - 1;
    build.cycle_ok = true;
    build.progress = progress;
    build.context = context;
    for (uint32_t index = 0, value = 1; index < build.ring.half; ++index, value *= parameters->p) {
        build.power[index] = value;
    }

    // Chunks: an even split over the threads, cut again at every multiple of 2^shift so that a
    // breakpoint is simply a chunk's starting row position.
    uint64_t *starts = malloc(((uint64_t)threads + nbp + 2) * sizeof *starts);
    uint32_t chunks = 0;
    if (starts == NULL) {
        *error = "host allocation failed";
        return false;
    }
    for (uint32_t index = 0; index < threads; ++index) {
        starts[chunks++] = 1 + (q - 1) * index / threads;   // q < 2^36, threads <= 1024: no overflow
    }
    for (uint64_t high = 1; high <= nbp; ++high) {
        starts[chunks++] = high << shift;
    }
    qsort(starts, chunks, sizeof *starts, compare_u64);
    uint32_t unique = 0;
    for (uint32_t index = 0; index < chunks; ++index) {
        if ((unique == 0 || starts[unique - 1] != starts[index]) && starts[index] < q) {
            starts[unique++] = starts[index];
        }
    }
    chunks = unique;
    starts[chunks] = q;
    build.starts = starts;
    build.chunks = chunks;

    build.fold = malloc((uint64_t)parameters->p * parameters->r * sizeof *build.fold);
    build.counts = calloc((uint64_t)chunks * budget, sizeof *build.counts);
    uint32_t *rows = malloc(limit * f * sizeof *rows);
    uint32_t *bp = nbp ? malloc(limit * nbp * sizeof *bp) : NULL;
    if (build.fold == NULL || build.counts == NULL || rows == NULL || (nbp && bp == NULL)) {
        free(build.fold); free(build.counts); free(rows); free(bp); free(starts);
        *error = "host allocation failed";
        return false;
    }
    for (uint32_t carry = 0; carry < parameters->p; ++carry) {
        for (uint32_t index = 0; index < parameters->r; ++index) {
            uint64_t product = (uint64_t)carry * build.ring.poly[index] % parameters->p;
            build.fold[carry * parameters->r + index] = (uint32_t)((parameters->p - product) % parameters->p);
        }
    }

    bool ok = run_pass(&build);
    // Prefix sums: each chunk's labels follow the earlier chunks' labels in its row. Label 0
    // (the zero element) is in cell 0 and comes first.
    for (uint64_t cell = 0; ok && cell < budget; ++cell) {
        uint64_t offset = cell == 0 ? 1 : 0;
        for (uint32_t chunk = 0; chunk < chunks; ++chunk) {
            uint32_t *slot = &build.counts[(uint64_t)chunk * budget + cell];
            uint32_t count = *slot;
            *slot = (uint32_t)offset;
            offset += count;
            uint64_t start = starts[chunk];
            if (cell < limit && nbp != 0 && start % (UINT64_C(1) << shift) == 0) {
                bp[cell * nbp + (start >> shift) - 1] = build.counts[(uint64_t)chunk * budget + cell];
            }
        }
        if (offset != f) {
            ok = false;
            *error = "field partition cardinality invariant failed";
        }
    }
    if (ok && !build.cycle_ok) {
        ok = false;
        *error = "generator cycle does not close";
    }
    if (ok) {
        rows[0] = 0;
        build.rows = rows;
        build.writing = true;
        ok = run_pass(&build);
        if (!ok) *error = "cannot start field threads";
    } else if (*error == NULL) {
        *error = "cannot start field threads";
    }
    free(build.fold);
    free(build.counts);
    free(starts);
    if (!ok) {
        free(rows);
        free(bp);
        return false;
    }
    output->rows = rows;
    output->bp = bp;
    output->limit = limit;
    output->f = (uint32_t)f;
    output->nbp = nbp;
    output->shift = shift;

    // Spot-check: label z must sit in the row of the cell of X^(z-1), computed independently.
    uint64_t state = UINT64_C(0x9E3779B97F4A7C15) ^ q;
    for (uint32_t sample = 0; sample < 2000; ++sample) {
        state = state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
        uint64_t label = 1 + (state >> 11) % (q - 1);
        element_t element = power_x(&build.ring, label - 1);
        uint64_t cell = cell_of(&build, &element);
        if (cell >= limit) {
            continue;
        }
        uint32_t lo = 0, hi = (uint32_t)f;
        while (lo < hi) {
            uint32_t mid = lo + (hi - lo) / 2;
            if (fp_label(output, cell, mid) < label) lo = mid + 1; else hi = mid;
        }
        if (lo == f || fp_label(output, cell, lo) != label) {
            fp_free(output);
            *error = "field rows failed the spot check";
            return false;
        }
    }
    return true;
}

void fp_free(fp_rows_t *rows) {
    free(rows->rows);
    free(rows->bp);
    memset(rows, 0, sizeof *rows);
}
