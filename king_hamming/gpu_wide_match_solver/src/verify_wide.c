#define _GNU_SOURCE

// kh_verify_wide: streaming verifier for KHM1 full matchings too large for kh_verify_khm1
// (q up to 2^40, field rows larger than memory). Same contract as kh_verify_khm1, plus passes and
// threads. It is deliberately independent of the solvers: it shares no code with field_walk.c,
// dp_solver/src/field.c or any matching kernel. Like kh_verify_khm1 and the Python verifier
// (matching_solver/artifacts.py), it keeps field elements as packed base-p integers; divisions
// by p and F use precomputed reciprocals instead of digit vectors.
//
// Usage: kh_verify_wide P R C0,...,Cr BLOCKS.txt FILE OFFSET [--threads N] [--row-bytes N]
// BLOCKS.txt: "<runs>\n" then one "<a> <copies>" line per run, as for kh_match_kernel.
// FILE at OFFSET holds n packed choices of bit_length(F-1) bits, low bit first, then zero padding.
// Prints {"assigned":N,"requests":n,"passes":k} and exits 0, or prints a reason to stderr and exits 1.
//
// One count walk over all q labels (threads take chunks; a chunk starts at X^(z-1), computed by
// exponentiation, and must end where the next one starts) fixes where each chunk's labels go in
// every cell row. Then, pass by pass, a placement walk builds the rows of one range of cells and
// the certificate's choices for those cells are read and checked: every edge exists and every
// right endpoint is used once (a q-bit bitmap). Memory: 4*chunks*budget offsets, q/8 bitmap, and
// per pass 4*(F + breakpoints) bytes per cell (--row-bytes; default: every used cell in one pass).

#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_Q (UINT64_C(1) << 40)

static int fail(const char *message) {
    fprintf(stderr, "kh_verify_wide: %s\n", message);
    return 1;
}

static bool parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    *value = strtoull(text, &end, 10);
    return end != text && *end == '\0';
}

// ----- packed arithmetic ----------------------------------------------------------------------

typedef struct {
    uint64_t p, r, q, qm1, f, budget, half, top_place;
    uint64_t poly[32];
    uint64_t magic_p, magic_f;   // floor(2^64 / d) + 1: exact quotients for n < 2^40, d < 2^24
    double inverse_top;          // 1 / p^(r-1), corrected after use
    uint32_t *subtract;          // subtract[top * r + j] = top * c_j mod p
} field_t;

static inline uint64_t quotient(uint64_t n, uint64_t magic) {
    return (uint64_t)(((__uint128_t)n * magic) >> 64);
}

// X * element, as kh_verify_khm1: shift the base-p digits up and fold the top digit back through
// X^r = -(c_0 + ... + c_{r-1} X^{r-1}).
static inline uint64_t times_x(const field_t *field, uint64_t current) {
    uint64_t top = (uint64_t)((double)current * field->inverse_top);
    while (top * field->top_place > current) --top;
    while ((top + 1) * field->top_place <= current) ++top;
    uint64_t rest = (current - top * field->top_place) * field->p;
    if (top == 0) return rest;
    const uint32_t *subtract = field->subtract + top * field->r;
    uint64_t result = 0, place = 1, p = field->p;
    for (uint64_t j = 0; j < field->r; ++j, place *= p) {
        uint64_t next = quotient(rest, field->magic_p), digit = rest - next * p;
        digit = digit >= subtract[j] ? digit - subtract[j] : digit + p - subtract[j];
        result += digit * place;
        rest = next;
    }
    return result;
}

// Cell of an element: (sum of its high r - half digits mod p) * F + its low half digits.
static inline uint64_t cell_of(const field_t *field, uint64_t element) {
    uint64_t high = quotient(element, field->magic_f), suffix = element - high * field->f, prefix = 0;
    while (high != 0) {
        uint64_t next = quotient(high, field->magic_p);
        prefix += high - next * field->p;
        high = next;
    }
    return (prefix % field->p) * field->f + suffix;
}

static uint64_t multiply(const field_t *field, uint64_t a, uint64_t b) {
    uint64_t p = field->p, r = field->r, da[32], db[32], product[64] = {0};
    for (uint64_t j = 0; j < r; ++j) { da[j] = a % p; a /= p; db[j] = b % p; b /= p; }
    for (uint64_t i = 0; i < r; ++i) {
        for (uint64_t j = 0; j < r; ++j) product[i + j] = (product[i + j] + da[i] * db[j]) % p;
    }
    for (uint64_t degree = 2 * r - 2; degree >= r; --degree) {
        uint64_t factor = product[degree];
        for (uint64_t j = 0; j < r; ++j) {
            product[degree - r + j] = (product[degree - r + j] + p - factor * field->poly[j] % p) % p;
        }
        product[degree] = 0;
    }
    uint64_t result = 0;
    for (uint64_t j = r; j-- > 0;) result = result * p + product[j];
    return result;
}

static uint64_t power_x(const field_t *field, uint64_t exponent) {
    uint64_t result = 1, base = field->p;   // X is the packed element p (digit 1 at place 1)
    for (; exponent != 0; exponent >>= 1) {
        if (exponent & 1) result = multiply(field, result, base);
        base = multiply(field, base, base);
    }
    return result;
}

// ----- walks ----------------------------------------------------------------------------------

typedef struct {
    const field_t *field;
    uint64_t *starts;          // chunk c covers labels [starts[c], starts[c+1]); starts[chunks] = q
    uint32_t chunks, threads, split, nbp;
    uint32_t *offsets;         // chunks * budget: counts, then each chunk's first position per cell
    // placement of cells [lo, hi)
    uint64_t lo, hi;
    uint32_t *rows, *pos, *bp, low_mask;   // bp: the pass's breakpoints, nbp per row
    uint32_t next;             // atomic chunk counter
    bool broken;               // a chunk did not end where the next begins, or the cycle did not close
} walk_t;

static void *walk_worker(void *raw) {
    walk_t *walk = raw;
    const field_t *field = walk->field;
    uint64_t budget = field->budget, f = field->f, width = walk->hi - walk->lo;
    for (;;) {
        uint32_t chunk = __atomic_fetch_add(&walk->next, 1, __ATOMIC_RELAXED);
        if (chunk >= walk->chunks) break;
        uint64_t first = walk->starts[chunk], last = walk->starts[chunk + 1];
        uint64_t element = power_x(field, first - 1);   // label z is X^(z-1)
        if (walk->rows == NULL) {
            uint32_t *counts = walk->offsets + (uint64_t)chunk * budget;
            for (uint64_t label = first; label < last; ++label) {
                ++counts[cell_of(field, element)];
                element = times_x(field, element);
            }
            // Stepping must agree with exponentiation at every chunk boundary, and close the cycle.
            uint64_t expected = last == field->q ? 1 : power_x(field, last - 1);
            if (element != expected) walk->broken = true;
        } else {
            uint32_t *pos = walk->pos + (uint64_t)chunk * width;
            for (uint64_t label = first; label < last; ++label) {
                uint64_t cell = cell_of(field, element);
                if (cell >= walk->lo && cell < walk->hi) {
                    uint64_t slot = cell - walk->lo;
                    walk->rows[slot * f + pos[slot]++] = (uint32_t)(label & walk->low_mask);
                }
                element = times_x(field, element);
            }
        }
    }
    return NULL;
}

static bool run_walk(walk_t *walk) {
    pthread_t threads[1024];
    uint32_t started = 0;
    walk->next = 0;
    for (; started < walk->threads; ++started) {
        if (pthread_create(&threads[started], NULL, walk_worker, walk) != 0) break;
    }
    for (uint32_t index = 0; index < started; ++index) pthread_join(threads[index], NULL);
    return started == walk->threads && !walk->broken;
}

// ----- checking the certificate ---------------------------------------------------------------

typedef struct {
    uint64_t first;    // canonical index of the segment's first request
    uint64_t count;
    uint64_t coset;
    uint64_t cell;     // its first cell
} segment_t;

typedef struct {
    const field_t *field;
    const walk_t *walk;
    const segment_t *segments;
    uint64_t nsegments;
    const char *path;
    uint64_t offset;
    uint32_t bits;
    uint8_t *used;
    uint64_t next, assigned;   // atomic
    const char *error;
} check_t;

static void *check_worker(void *raw) {
    check_t *check = raw;
    const field_t *field = check->field;
    const walk_t *walk = check->walk;
    uint64_t f = field->f, qm1 = field->qm1, mask = (UINT64_C(1) << check->bits) - 1, assigned = 0;
    uint32_t nbp = walk->nbp, split = walk->split;
    FILE *input = fopen(check->path, "rb");
    uint8_t *buffer = NULL;
    size_t capacity = 0;
    if (input == NULL) check->error = "cannot read the certificate";
    while (input != NULL && check->error == NULL) {
        uint64_t index = __atomic_fetch_add(&check->next, 1, __ATOMIC_RELAXED);
        if (index >= check->nsegments) break;
        const segment_t *segment = &check->segments[index];
        uint64_t bit = segment->first * check->bits, end = bit + segment->count * check->bits;
        uint64_t byte0 = bit >> 3, length = ((end + 7) >> 3) - byte0;
        if (length + 8 > capacity) {
            capacity = length + 8;
            uint8_t *grown = realloc(buffer, capacity);
            if (grown == NULL) { check->error = "allocation failed"; break; }
            buffer = grown;
        }
        if (pread(fileno(input), buffer, length, (off_t)(check->offset + byte0)) != (ssize_t)length) {
            check->error = "truncated choices";
            break;
        }
        uint64_t accumulator = buffer[0] >> (bit & 7);
        uint32_t available = 8 - (uint32_t)(bit & 7);
        size_t at = 1;
        uint64_t coset = segment->coset % qm1;
        for (uint64_t k = 0; k < segment->count; ++k) {
            while (available < check->bits) {
                accumulator |= (uint64_t)buffer[at++] << available;
                available += 8;
            }
            uint64_t choice = accumulator & mask;
            accumulator >>= check->bits;
            available -= check->bits;
            if (choice >= f) { check->error = "neighbor index out of range"; break; }
            uint64_t slot = segment->cell + k - walk->lo;
            uint64_t label = walk->rows[slot * f + choice];
            const uint32_t *bp = walk->bp + slot * nbp;   // ascending
            for (uint32_t h = 0; h < nbp && choice >= bp[h]; ++h) label += UINT64_C(1) << split;
            uint64_t right = label == 0 ? 0 : 1 + (label - 1 + qm1 - coset) % qm1;
            uint8_t flag = (uint8_t)(1u << (right & 7));
            if (__atomic_fetch_or(&check->used[right >> 3], flag, __ATOMIC_RELAXED) & flag) {
                check->error = "matching repeats a right endpoint";
                break;
            }
            ++assigned;
        }
    }
    __atomic_fetch_add(&check->assigned, assigned, __ATOMIC_RELAXED);
    free(buffer);
    if (input != NULL) fclose(input);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc == 1) {
        puts("Verify the edges of a KHM1 full matching in passes, for fields up to q < 2^40.\n"
             "Usage: kh_verify_wide P R C0,...,Cr BLOCKS.txt FILE OFFSET [--threads N] [--row-bytes N]\n"
             "Example: kh_verify_wide 3 3 1,2,0,1 blocks.txt result.khmatch 52 --threads 8");
        return 0;
    }
    uint64_t p, r, offset, threads = 1, row_bytes = 0;
    if (argc < 7 || !parse_u64(argv[1], &p) || !parse_u64(argv[2], &r) || !parse_u64(argv[6], &offset) ||
        p < 3 || p > 65535 || r < 3 || r > 31) {
        return fail("invalid arguments");
    }
    for (int index = 7; index + 1 < argc + 1; index += 2) {
        uint64_t value;
        if (index + 1 >= argc || !parse_u64(argv[index + 1], &value)) return fail("invalid option");
        if (!strcmp(argv[index], "--threads") && value >= 1 && value <= 1024) threads = value;
        else if (!strcmp(argv[index], "--row-bytes") && value > 0) row_bytes = value;
        else return fail("invalid option");
    }
    field_t field = {0};
    field.p = p;
    field.r = r;
    field.q = 1;
    for (uint64_t i = 0; i < r; ++i) {
        field.q *= p;
        if (field.q >= MAX_Q) return fail("field too large");
    }
    field.f = 1;
    for (uint64_t i = 0; i < r / 2; ++i) field.f *= p;
    if (field.f >= (UINT64_C(1) << 24)) return fail("field too large");
    field.qm1 = field.q - 1;
    field.budget = p * field.f;
    field.half = r / 2;
    field.top_place = field.q / p;
    field.magic_p = UINT64_MAX / p + 1;
    field.magic_f = UINT64_MAX / field.f + 1;
    field.inverse_top = 1.0 / (double)field.top_place;
    const char *cursor = argv[3];
    for (uint64_t i = 0; i <= r; ++i) {
        char *end = NULL;
        field.poly[i] = strtoull(cursor, &end, 10);
        if (end == cursor || field.poly[i] >= p || (i < r ? *end != ',' : *end != '\0')) return fail("invalid polynomial");
        cursor = end + 1;
    }
    if (field.poly[r] != 1) return fail("polynomial is not monic");
    field.subtract = malloc(p * r * sizeof *field.subtract);
    if (field.subtract == NULL) return fail("allocation failed");
    for (uint64_t top = 0; top < p; ++top) {
        for (uint64_t j = 0; j < r; ++j) field.subtract[top * r + j] = (uint32_t)(top * field.poly[j] % p);
    }

    FILE *blocks = fopen(argv[4], "r");
    uint64_t runs = 0, amax = 0, n = 0, cosets = 0, f = field.f, budget = field.budget;
    if (blocks == NULL || fscanf(blocks, "%" SCNu64, &runs) != 1 || runs == 0 || runs > budget) return fail("invalid blocks file");
    uint64_t *run_a = calloc(runs, 8), *run_copies = calloc(runs, 8);
    if (run_a == NULL || run_copies == NULL) return fail("allocation failed");
    for (uint64_t i = 0; i < runs; ++i) {
        if (fscanf(blocks, "%" SCNu64 " %" SCNu64, &run_a[i], &run_copies[i]) != 2 || run_a[i] == 0 ||
            run_a[i] > p || run_copies[i] == 0) return fail("invalid blocks file");
        if (run_a[i] > amax) amax = run_a[i];
        n += run_a[i] * run_copies[i] * f;
        cosets += run_copies[i];
        if (n > field.q || cosets >= field.qm1) return fail("blocks exceed the field");
    }
    fclose(blocks);
    uint64_t limit = amax * f;   // requests use only cells 0 .. amax*F-1

    // Labels are stored as low `split` bits; a row's breakpoints give the high part.
    // KH_VERIFY_SPLIT_BITS (tests only) splits lower, to exercise this on small fields.
    uint64_t split = 32;
    const char *split_text = getenv("KH_VERIFY_SPLIT_BITS");
    if (split_text != NULL && (!parse_u64(split_text, &split) || split == 0 || split > 32)) return fail("invalid KH_VERIFY_SPLIT_BITS");
    uint64_t nbp = field.qm1 >> split;
    if (nbp > 4096) return fail("KH_VERIFY_SPLIT_BITS is too small for this field");

    // Chunks: two per thread, cut again at every multiple of 2^split so a breakpoint is a chunk's
    // first position in the row.
    walk_t walk = {0};
    walk.field = &field;
    walk.threads = (uint32_t)threads;
    walk.split = (uint32_t)split;
    walk.nbp = (uint32_t)nbp;
    walk.low_mask = split == 32 ? UINT32_MAX : (UINT32_C(1) << split) - 1;
    walk.starts = malloc((2 * threads + nbp + 2) * sizeof *walk.starts);
    if (walk.starts == NULL) return fail("allocation failed");
    uint32_t chunks = 0;
    for (uint64_t index = 0; index < 2 * threads; ++index) walk.starts[chunks++] = 1 + field.qm1 * index / (2 * threads);
    for (uint64_t h = 1; h <= nbp; ++h) walk.starts[chunks++] = h << split;
    for (uint32_t a = 1; a < chunks; ++a) {   // insertion sort, then drop repeats
        uint64_t value = walk.starts[a];
        uint32_t b = a;
        while (b > 0 && walk.starts[b - 1] > value) { walk.starts[b] = walk.starts[b - 1]; --b; }
        walk.starts[b] = value;
    }
    uint32_t unique = 0;
    for (uint32_t a = 0; a < chunks; ++a) {
        if ((unique == 0 || walk.starts[unique - 1] != walk.starts[a]) && walk.starts[a] < field.q) walk.starts[unique++] = walk.starts[a];
    }
    walk.chunks = unique;
    walk.starts[unique] = field.q;
    walk.offsets = calloc((uint64_t)walk.chunks * budget, sizeof *walk.offsets);
    if (walk.offsets == NULL) return fail("allocation failed");
    if (!run_walk(&walk)) return fail("invalid primitive cycle");
    for (uint64_t cell = 0; cell < budget; ++cell) {
        uint64_t total = cell == 0 ? 1 : 0;   // label 0 heads cell 0
        for (uint32_t chunk = 0; chunk < walk.chunks; ++chunk) {
            uint32_t *slot = &walk.offsets[(uint64_t)chunk * budget + cell];
            uint32_t count = *slot;
            *slot = (uint32_t)total;
            total += count;
        }
        if (total != f) return fail("invalid SUD partition");
    }

    // Passes over cell ranges of the used cells.
    uint64_t per_cell = 4 * (f + nbp) + 4 * (uint64_t)walk.chunks;
    uint64_t width = row_bytes ? row_bytes / per_cell : limit;
    if (width == 0) return fail("--row-bytes holds less than one row");
    if (width > limit) width = limit;
    uint32_t bits = 0;
    for (uint64_t value = f - 1; value != 0; value >>= 1) ++bits;
    uint8_t *used = calloc((field.q + 7) / 8, 1);
    walk.rows = malloc(width * f * sizeof *walk.rows);
    // pos holds each chunk's running positions, then the pass's breakpoints.
    walk.pos = malloc(((uint64_t)walk.chunks * width + width * nbp + 1) * sizeof *walk.pos);
    segment_t *segments = malloc((cosets + 1) * sizeof *segments);
    if (used == NULL || walk.rows == NULL || walk.pos == NULL || segments == NULL) return fail("allocation failed");
    uint64_t assigned = 0, passes = 0;
    for (uint64_t lo = 0; lo < limit; lo += width) {
        uint64_t hi = lo + width < limit ? lo + width : limit, cells = hi - lo;
        walk.lo = lo;
        walk.hi = hi;
        uint32_t *breakpoints = walk.pos + (uint64_t)walk.chunks * cells;
        walk.bp = breakpoints;
        for (uint32_t chunk = 0; chunk < walk.chunks; ++chunk) {
            for (uint64_t slot = 0; slot < cells; ++slot) {
                walk.pos[(uint64_t)chunk * cells + slot] = walk.offsets[(uint64_t)chunk * budget + lo + slot];
            }
            uint64_t start = walk.starts[chunk];
            if (nbp && start % (UINT64_C(1) << split) == 0) {
                for (uint64_t slot = 0; slot < cells; ++slot) {
                    breakpoints[slot * nbp + (start >> split) - 1] = walk.offsets[(uint64_t)chunk * budget + lo + slot];
                }
            }
        }
        if (lo == 0) walk.rows[0] = 0;
        if (!run_walk(&walk)) return fail("invalid primitive cycle");
        for (uint32_t chunk = 0; chunk < walk.chunks; ++chunk) {
            for (uint64_t slot = 0; slot < cells; ++slot) {
                uint64_t end = chunk + 1 < walk.chunks ? walk.offsets[(uint64_t)(chunk + 1) * budget + lo + slot] : f;
                if (walk.pos[(uint64_t)chunk * cells + slot] != end) return fail("invalid SUD bucket size");
            }
        }
        // The certificate's requests for these cells: one segment per (run, copy).
        uint64_t nsegments = 0, base = 0, coset = 0;
        for (uint64_t run = 0; run < runs; ++run) {
            uint64_t a_f = run_a[run] * f;
            for (uint64_t copy = 0; copy < run_copies[run]; ++copy, ++coset) {
                if (lo < a_f) {
                    segments[nsegments++] = (segment_t){base + copy * a_f + lo, (hi < a_f ? hi : a_f) - lo, coset, lo};
                }
            }
            base += run_copies[run] * a_f;
        }
        check_t check = {&field, &walk, segments, nsegments, argv[5], offset, bits, used, 0, 0, NULL};
        pthread_t workers[1024];
        uint32_t started = 0;
        for (; started < threads; ++started) {
            if (pthread_create(&workers[started], NULL, check_worker, &check) != 0) break;
        }
        for (uint32_t index = 0; index < started; ++index) pthread_join(workers[index], NULL);
        if (started != threads) return fail("cannot start threads");
        if (check.error != NULL) return fail(check.error);
        assigned += check.assigned;
        ++passes;
    }
    if (assigned != n) return fail("certificate does not cover every request");
    // Padding after the last choice must be zero.
    uint32_t tail = (uint32_t)((n * bits) & 7);
    if (tail) {
        FILE *input = fopen(argv[5], "rb");
        uint8_t last = 0;
        if (input == NULL || pread(fileno(input), &last, 1, (off_t)(offset + (n * bits) / 8)) != 1) return fail("truncated choices");
        fclose(input);
        if (last >> tail) return fail("nonzero padding");
    }
    printf("{\"assigned\":%" PRIu64 ",\"requests\":%" PRIu64 ",\"passes\":%" PRIu64 "}\n", assigned, n, passes);
    return 0;
}
