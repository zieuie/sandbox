#define _POSIX_C_SOURCE 200809L

#include "kh_sha256.h"

#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

typedef struct {
    uint32_t a, b, t, repeat;
} run_t;

typedef struct {
    uint32_t p, r, q, f, budget, run_count, coset_count;
    uint64_t theta, request_count;
    run_t *runs;
    unsigned char digest[32];
} dp_t;

typedef struct {
    uint32_t *polynomial;
    uint32_t *choices;
    uint64_t count;
} match_t;

typedef struct {
    const unsigned char *bytes;
    size_t position, end;
} reader_t;

typedef struct {
    const uint32_t *rows;
    uint64_t row_count;
    uint32_t width;
    uint32_t thread_index, thread_count;
    uint64_t pairs;
    bool failed;
    uint64_t left, right;
    uint32_t distance;
    uint64_t closest_left, closest_right;
    uint32_t minimum;
} compare_task_t;

static void usage(FILE *stream) {
    fprintf(stream,
        "usage: kh_verify_rows [--threads N] [--max-rows N] [--max-bytes N] "
        "[--max-comparisons N] [--render PATH] [--explain] DP.khdp MATCH.khmatch\n");
}

static bool checked_add_u64(uint64_t a, uint64_t b, uint64_t *out) {
    if (UINT64_MAX - a < b) return false;
    *out = a + b;
    return true;
}

static bool checked_mul_u64(uint64_t a, uint64_t b, uint64_t *out) {
    if (a != 0 && b > UINT64_MAX / a) return false;
    *out = a * b;
    return true;
}

static bool power_u32(uint32_t base, uint32_t exponent, uint32_t *out) {
    uint64_t value = 1;
    for (uint32_t i = 0; i < exponent; ++i) {
        value *= base;
        if (value > UINT32_MAX) return false;
    }
    *out = (uint32_t)value;
    return true;
}

static bool read_file(const char *path, unsigned char **bytes, size_t *size) {
    struct stat status;
    FILE *file = NULL;
    *bytes = NULL;
    *size = 0;
    if (stat(path, &status) != 0 || status.st_size < 0 ||
            (uint64_t)status.st_size > 16u * 1024u * 1024u) return false;
    file = fopen(path, "rb");
    if (file == NULL) return false;
    *size = (size_t)status.st_size;
    *bytes = malloc(*size ? *size : 1);
    bool ok = *bytes != NULL && fread(*bytes, 1, *size, file) == *size &&
              fgetc(file) == EOF && !ferror(file);
    if (fclose(file) != 0) ok = false;
    if (!ok) {
        free(*bytes);
        *bytes = NULL;
    }
    return ok;
}

static bool checksum_ok(const unsigned char *bytes, size_t size) {
    if (size < 32) return false;
    kh_sha256_t checksum;
    unsigned char digest[32];
    kh_sha256_init(&checksum);
    kh_sha256_update(&checksum, bytes, size - 32);
    kh_sha256_final(&checksum, digest);
    return memcmp(digest, bytes + size - 32, 32) == 0;
}

static bool whole_digest(const unsigned char *bytes, size_t size,
                         unsigned char digest[32]) {
    kh_sha256_t checksum;
    kh_sha256_init(&checksum);
    kh_sha256_update(&checksum, bytes, size);
    kh_sha256_final(&checksum, digest);
    return true;
}

static bool read_uint(reader_t *reader, uint64_t *out) {
    uint64_t value = 0;
    for (uint32_t shift = 0; shift < 64; shift += 7) {
        if (reader->position >= reader->end) return false;
        unsigned char byte = reader->bytes[reader->position++];
        if (shift == 63 && byte > 1) return false;
        value |= (uint64_t)(byte & 127u) << shift;
        if (byte < 128) {
            if (shift != 0 && byte == 0) return false;
            *out = value;
            return true;
        }
    }
    return false;
}

static void dp_free(dp_t *dp) {
    free(dp->runs);
    memset(dp, 0, sizeof *dp);
}

static bool dp_read(const char *path, dp_t *dp) {
    unsigned char *bytes = NULL;
    size_t size = 0;
    memset(dp, 0, sizeof *dp);
    if (!read_file(path, &bytes, &size) || size < 40 ||
            memcmp(bytes, "KHD1", 4) != 0 || !checksum_ok(bytes, size)) {
        free(bytes);
        fprintf(stderr, "invalid KHD1 file: %s\n", path);
        return false;
    }
    whole_digest(bytes, size, dp->digest);
    reader_t reader = {bytes, 4, size - 32};
    uint64_t p, r, theta, count;
    bool ok = read_uint(&reader, &p) && read_uint(&reader, &r) &&
              read_uint(&reader, &theta) && read_uint(&reader, &count) &&
              p >= 2 && p <= 1621 && r >= 3 && r <= 31 && (r & 1u) &&
              p <= UINT32_MAX && r <= UINT32_MAX;
    uint32_t q = 0, f = 0;
    if (ok) ok = power_u32((uint32_t)p, (uint32_t)r, &q) &&
                 power_u32((uint32_t)p, (uint32_t)r / 2, &f) &&
                 p * (uint64_t)f <= UINT32_MAX && count > 0 &&
                 count <= p * (uint64_t)f && count <= UINT32_MAX;
    if (!ok) {
        free(bytes);
        fprintf(stderr, "invalid KHD1 dimensions\n");
        return false;
    }
    dp->p = (uint32_t)p; dp->r = (uint32_t)r; dp->q = q; dp->f = f;
    dp->budget = (uint32_t)(p * f); dp->theta = theta;
    dp->run_count = (uint32_t)count;
    dp->runs = calloc(dp->run_count, sizeof *dp->runs);
    if (dp->runs == NULL) ok = false;
    uint64_t used_a = 0, used_b = 0, gain = 0, cosets = 0, requests = 0;
    run_t previous = {0, 0, 0, 0};
    for (uint32_t i = 0; ok && i < dp->run_count; ++i) {
        uint64_t a, b, t, repeat, copies, contribution;
        ok = read_uint(&reader, &a) && read_uint(&reader, &b) &&
             read_uint(&reader, &t) && read_uint(&reader, &repeat) &&
             a >= 1 && a <= p && b >= 1 && b <= p && t >= 1 && t <= p &&
             repeat >= 1 && repeat <= dp->budget &&
             (i == 0 || a != previous.a || b != previous.b || t != previous.t) &&
             checked_mul_u64(t, repeat, &copies);
        if (!ok) break;
        run_t run = {(uint32_t)a, (uint32_t)b, (uint32_t)t, (uint32_t)repeat};
        dp->runs[i] = run;
        previous = run;
        uint64_t residues = 0;
        bool seen[1621] = {false};
        for (uint32_t g = 0; g < run.a; ++g) {
            for (uint32_t h = 0; h < run.b; ++h) {
                uint32_t residue = (h * run.t + dp->p - g) % dp->p;
                if (!seen[residue]) { seen[residue] = true; ++residues; }
            }
        }
        ok = checked_mul_u64(a, copies, &contribution) &&
             checked_add_u64(used_a, contribution, &used_a) &&
             checked_mul_u64(b, copies, &contribution) &&
             checked_add_u64(used_b, contribution, &used_b) &&
             checked_add_u64(cosets, copies, &cosets) &&
             checked_mul_u64(t, residues, &contribution) &&
             checked_mul_u64(contribution, repeat, &contribution) &&
             checked_add_u64(gain, contribution, &gain) &&
             checked_mul_u64(a, copies, &contribution) &&
             checked_mul_u64(contribution, f, &contribution) &&
             checked_add_u64(requests, contribution, &requests);
    }
    ok = ok && reader.position == reader.end && used_a <= dp->budget &&
         used_b <= dp->budget && gain == dp->theta && cosets + 1 < dp->q &&
         cosets <= UINT32_MAX;
    if (ok) {
        dp->coset_count = (uint32_t)cosets;
        dp->request_count = requests;
    } else {
        fprintf(stderr, "invalid KHD1 runs or score\n");
        dp_free(dp);
    }
    free(bytes);
    return ok;
}

static void match_free(match_t *match) {
    free(match->polynomial);
    free(match->choices);
    memset(match, 0, sizeof *match);
}

static bool match_read(const char *path, const dp_t *dp, match_t *match) {
    unsigned char *bytes = NULL;
    size_t size = 0;
    memset(match, 0, sizeof *match);
    if (!read_file(path, &bytes, &size) || size < 68 ||
            memcmp(bytes, "KHM1", 4) != 0 || !checksum_ok(bytes, size) ||
            memcmp(bytes + 4, dp->digest, 32) != 0) {
        free(bytes);
        fprintf(stderr, "invalid KHM1 file or wrong KHD1 dependency: %s\n", path);
        return false;
    }
    reader_t reader = {bytes, 36, size - 32};
    uint64_t p, r, status, count, matched;
    bool ok = read_uint(&reader, &p) && read_uint(&reader, &r) &&
              p == dp->p && r == dp->r;
    match->polynomial = calloc((size_t)dp->r + 1, sizeof *match->polynomial);
    for (uint32_t i = 0; ok && i <= dp->r; ++i) {
        uint64_t coefficient;
        ok = read_uint(&reader, &coefficient) && coefficient < dp->p;
        if (ok) match->polynomial[i] = (uint32_t)coefficient;
    }
    ok = ok && read_uint(&reader, &status) && read_uint(&reader, &count) &&
         read_uint(&reader, &matched) && status == 0 &&
         count == dp->request_count && matched == count;
    uint32_t bits = 0;
    while ((UINT64_C(1) << bits) < dp->f) ++bits;
    uint64_t bit_count = 0, byte_count = 0;
    ok = ok && checked_mul_u64(count, bits, &bit_count) &&
         checked_add_u64(bit_count, 7, &byte_count);
    byte_count /= 8;
    ok = ok && byte_count <= SIZE_MAX && reader.position + (size_t)byte_count == reader.end;
    if (ok && count > SIZE_MAX / sizeof(uint32_t)) ok = false;
    if (ok) match->choices = malloc((size_t)count * sizeof *match->choices);
    if (ok && count && match->choices == NULL) ok = false;
    uint64_t accumulator = 0;
    uint32_t available = 0;
    for (uint64_t i = 0; ok && i < count; ++i) {
        while (available < bits) {
            if (reader.position >= reader.end || available > 56) { ok = false; break; }
            accumulator |= (uint64_t)reader.bytes[reader.position++] << available;
            available += 8;
        }
        if (!ok) break;
        uint32_t choice = (uint32_t)(accumulator & ((UINT64_C(1) << bits) - 1));
        accumulator >>= bits;
        available -= bits;
        if (choice >= dp->f) ok = false;
        else match->choices[i] = choice;
    }
    ok = ok && reader.position == reader.end && accumulator == 0;
    if (ok && available && reader.end) {
        unsigned used = (unsigned)(bit_count & 7u);
        if (used && (reader.bytes[reader.end - 1] >> used) != 0) ok = false;
    }
    if (ok) match->count = count;
    else {
        fprintf(stderr, "invalid or incomplete full KHM1 matching\n");
        match_free(match);
    }
    free(bytes);
    return ok;
}

static uint32_t residue_add(uint32_t left, uint32_t right, uint32_t p, uint32_t r) {
    uint32_t result = 0, place = 1;
    for (uint32_t i = 0; i < r; ++i) {
        result += ((left % p + right % p) % p) * place;
        left /= p; right /= p; place *= p;
    }
    return result;
}

static uint32_t residue_multiply(uint32_t left, uint32_t right, const dp_t *dp,
                                 const uint32_t *polynomial) {
    uint64_t coefficients[61] = {0};
    uint32_t a[31] = {0}, b[31] = {0};
    for (uint32_t i = 0; i < dp->r; ++i) {
        a[i] = left % dp->p; left /= dp->p;
        b[i] = right % dp->p; right /= dp->p;
    }
    for (uint32_t i = 0; i < dp->r; ++i)
        for (uint32_t j = 0; j < dp->r; ++j)
            coefficients[i + j] = (coefficients[i + j] + (uint64_t)a[i] * b[j]) % dp->p;
    for (uint32_t degree = 2 * dp->r - 2; degree >= dp->r; --degree) {
        uint64_t lead = coefficients[degree] % dp->p;
        for (uint32_t j = 0; j < dp->r; ++j) {
            uint64_t subtract = lead * polynomial[j] % dp->p;
            coefficients[degree - dp->r + j] =
                (coefficients[degree - dp->r + j] + dp->p - subtract) % dp->p;
        }
    }
    uint32_t result = 0, place = 1;
    for (uint32_t i = 0; i < dp->r; ++i) {
        result += (uint32_t)coefficients[i] * place;
        place *= dp->p;
    }
    return result;
}

static bool field_build(const dp_t *dp, const uint32_t *polynomial,
                        uint32_t **elements_out, uint32_t **labels_out, uint32_t **cells_out) {
    if (polynomial[dp->r] != 1 || polynomial[0] == 0) return false;
    uint32_t *elements = malloc((size_t)dp->q * sizeof *elements);
    uint32_t *labels = malloc((size_t)dp->q * sizeof *labels);
    uint32_t *cells = malloc((size_t)dp->q * sizeof *cells);
    uint32_t *positions = calloc(dp->budget, sizeof *positions);
    if (!elements || !labels || !cells || !positions) {
        free(elements); free(labels); free(cells); free(positions); return false;
    }
    for (uint32_t i = 0; i < dp->q; ++i) labels[i] = UINT32_MAX;
    uint32_t current = 1;
    for (uint32_t label = 0; label < dp->q; ++label) {
        uint32_t element = label == 0 ? 0 : current;
        if (labels[element] != UINT32_MAX) { free(elements); free(labels); free(cells); free(positions); return false; }
        elements[label] = element; labels[element] = label;
        uint32_t suffix = element % dp->f, high = element / dp->f, prefix = 0;
        for (uint32_t i = dp->r / 2; i < dp->r; ++i) { prefix += high % dp->p; high /= dp->p; }
        uint32_t cell = (prefix % dp->p) * dp->f + suffix;
        if (positions[cell] >= dp->f) { free(elements); free(labels); free(cells); free(positions); return false; }
        cells[(uint64_t)cell * dp->f + positions[cell]++] = label;
        if (label) current = residue_multiply(current, dp->p, dp, polynomial);
    }
    bool ok = current == 1;
    for (uint32_t i = 0; ok && i < dp->budget; ++i) ok = positions[i] == dp->f;
    free(positions);
    if (!ok) { free(elements); free(labels); free(cells); return false; }
    *elements_out = elements; *labels_out = labels; *cells_out = cells;
    return true;
}

static bool build_partitions(const dp_t *dp, const match_t *match, const uint32_t *cells,
                             unsigned char **p_out, unsigned char **q_out) {
    uint64_t bytes;
    if (!checked_mul_u64(dp->coset_count, dp->q, &bytes) || bytes > SIZE_MAX) return false;
    unsigned char *psets = calloc((size_t)bytes, 1), *qsets = calloc((size_t)bytes, 1);
    unsigned char *occupied = calloc(dp->q, 1);
    if (!psets || !qsets || !occupied) { free(psets); free(qsets); free(occupied); return false; }
    uint64_t choice_index = 0, j = 0;
    uint32_t coset = 0;
    for (uint32_t ri = 0; ri < dp->run_count; ++ri) {
        run_t run = dp->runs[ri];
        for (uint32_t copy = 0; copy < run.repeat; ++copy) {
            for (uint32_t k = 0; k < run.b * run.t; ++k) {
                uint32_t quotient = (uint32_t)(j / dp->p), residue = (uint32_t)(j % dp->p);
                uint32_t cell = residue * dp->f + quotient;
                uint32_t target = coset + k % run.t;
                for (uint32_t x = 0; x < dp->f; ++x)
                    qsets[(uint64_t)target * dp->q + cells[(uint64_t)cell * dp->f + x]] = 1;
                ++j;
            }
            for (uint32_t block = 0; block < run.t; ++block) {
                uint32_t target = coset + block;
                for (uint32_t prefix = 0; prefix < run.a; ++prefix) {
                    for (uint32_t suffix = 0; suffix < dp->f; ++suffix) {
                        if (choice_index >= match->count) goto bad;
                        uint32_t choice = match->choices[choice_index++];
                        uint32_t label = cells[((uint64_t)prefix * dp->f + suffix) * dp->f + choice];
                        uint32_t right = label == 0 ? 0 : 1 + (label - 1 + (dp->q - 1) - target) % (dp->q - 1);
                        if (occupied[right]) {
                            fprintf(stderr, "matching repeats right endpoint %u at request %" PRIu64 "\n", right, choice_index - 1);
                            goto bad;
                        }
                        occupied[right] = 1;
                        psets[(uint64_t)target * dp->q + right] = 1;
                    }
                }
            }
            coset += run.t;
        }
    }
    free(occupied);
    if (choice_index != match->count || coset != dp->coset_count || j > dp->budget) goto bad_no_occupied;
    *p_out = psets; *q_out = qsets;
    return true;
bad:
    free(occupied);
bad_no_occupied:
    free(psets); free(qsets);
    return false;
}

static bool render_rows(const dp_t *dp, const match_t *match, const uint32_t *elements,
                        const uint32_t *labels, const unsigned char *psets,
                        const unsigned char *qsets, uint64_t max_rows, uint64_t max_bytes,
                        uint32_t **rows_out, uint64_t *row_count_out) {
    uint64_t gained = 0, row_count = 0, cells = 0, bytes = 0;
    if (!checked_mul_u64(dp->theta, dp->f, &gained) ||
            !checked_mul_u64(gained, dp->f, &gained) ||
            !checked_add_u64(gained, dp->q, &row_count) || row_count > max_rows ||
            !checked_mul_u64(row_count, (uint64_t)dp->q + 1, &cells) ||
            !checked_mul_u64(cells, sizeof(uint32_t), &bytes) || bytes > max_bytes || bytes > SIZE_MAX) {
        fprintf(stderr, "rendering exceeds limits: rows=%" PRIu64 " (limit=%" PRIu64 ")\n", row_count, max_rows);
        return false;
    }
    uint32_t *rows = malloc((size_t)bytes);
    if (rows == NULL) return false;
    uint64_t next = 0;
    for (uint32_t coset = 0; coset < dp->coset_count; ++coset) {
        uint32_t multiplier = elements[coset + 1];
        for (uint32_t translate = 0; translate < dp->q; ++translate) {
            if (next >= row_count) {
                fprintf(stderr, "coverage exceeds the DP score\n");
                free(rows); return false;
            }
            uint32_t *row = rows + next++ * ((uint64_t)dp->q + 1);
            uint32_t covered = UINT32_MAX;
            for (uint32_t position = 0; position < dp->q; ++position) {
                uint32_t product = residue_multiply(multiplier, elements[position], dp, match->polynomial);
                uint32_t value = labels[residue_add(product, elements[translate], dp->p, dp->r)];
                row[position] = value;
                if (covered == UINT32_MAX && psets[(uint64_t)coset * dp->q + position] &&
                        qsets[(uint64_t)coset * dp->q + value]) covered = position;
            }
            if (covered == UINT32_MAX) {
                --next;
            } else {
                row[dp->q] = row[covered];
                row[covered] = dp->q;
            }
        }
    }
    uint32_t multiplier = elements[dp->coset_count + 1];
    for (uint32_t translate = 0; translate < dp->q; ++translate) {
        if (next >= row_count) {
            fprintf(stderr, "coverage exceeds the DP score before the freebie\n");
            free(rows); return false;
        }
        uint32_t *row = rows + next++ * ((uint64_t)dp->q + 1);
        for (uint32_t position = 0; position < dp->q; ++position) {
            uint32_t product = residue_multiply(multiplier, elements[position], dp, match->polynomial);
            row[position] = labels[residue_add(product, elements[translate], dp->p, dp->r)];
        }
        row[dp->q] = dp->q;
    }
    if (next != row_count) {
        fprintf(stderr, "coverage count mismatch: rendered=%" PRIu64 " expected=%" PRIu64 "\n", next, row_count);
        free(rows);
        return false;
    }
    *rows_out = rows; *row_count_out = row_count;
    return true;
}

static void *compare_rows(void *argument) {
    compare_task_t *task = argument;
    for (uint64_t left = task->thread_index; left < task->row_count; left += task->thread_count) {
        const uint32_t *a = task->rows + left * task->width;
        for (uint64_t right = left + 1; right < task->row_count; ++right) {
            const uint32_t *b = task->rows + right * task->width;
            uint32_t distance = 0;
            for (uint32_t column = 0; column < task->width; ++column) distance += a[column] != b[column];
            ++task->pairs;
            if (distance < task->minimum) {
                task->minimum = distance;
                task->closest_left = left;
                task->closest_right = right;
            }
            if (distance + 1 < task->width) {
                task->failed = true; task->left = left; task->right = right; task->distance = distance;
                return NULL;
            }
        }
    }
    return NULL;
}

static bool rows_are_permutations(const uint32_t *rows, uint64_t row_count, uint32_t width) {
    unsigned char *seen = malloc(width);
    if (seen == NULL) return false;
    for (uint64_t row = 0; row < row_count; ++row) {
        memset(seen, 0, width);
        for (uint32_t column = 0; column < width; ++column) {
            uint32_t value = rows[row * width + column];
            if (value >= width || seen[value]) {
                fprintf(stderr, "row is not a permutation: row=%" PRIu64
                        " column=%u value=%u\n", row, column, value);
                free(seen);
                return false;
            }
            seen[value] = 1;
        }
    }
    free(seen);
    return true;
}

static bool render_text(const char *path, const uint32_t *rows,
                        uint64_t row_count, uint32_t width) {
    int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (descriptor < 0) return false;
    FILE *output = fdopen(descriptor, "w");
    bool ok = output != NULL;
    for (uint64_t row = 0; ok && row < row_count; ++row) {
        for (uint32_t column = 0; column < width; ++column) {
            if (fprintf(output, "%s%u", column ? " " : "", rows[row * width + column]) < 0) ok = false;
        }
        if (ok && fputc('\n', output) == EOF) ok = false;
    }
    if (output != NULL) {
        if (fflush(output) != 0 || fsync(fileno(output)) != 0) ok = false;
        if (fclose(output) != 0) ok = false;
    } else {
        close(descriptor);
    }
    if (!ok) unlink(path);
    return ok;
}

static bool parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    errno = 0;
    unsigned long long parsed = strtoull(text, &end, 10);
    if (errno || end == text || *end) return false;
    *value = (uint64_t)parsed;
    return true;
}

int main(int argc, char **argv) {
    uint64_t threads64 = 1, max_rows = 100000, max_bytes = UINT64_C(1) << 30,
             max_comparisons = UINT64_C(1000000000);
    const char *render_path = NULL;
    bool explain = false;
    int position = 1;
    while (position < argc && strncmp(argv[position], "--", 2) == 0) {
        const char *name = argv[position++];
        if (strcmp(name, "--help") == 0) { usage(stdout); return 0; }
        if (strcmp(name, "--explain") == 0) { explain = true; continue; }
        if (position == argc) { usage(stderr); return 2; }
        if (strcmp(name, "--render") == 0) { render_path = argv[position++]; continue; }
        uint64_t *target = strcmp(name, "--threads") == 0 ? &threads64 :
                           strcmp(name, "--max-rows") == 0 ? &max_rows :
                           strcmp(name, "--max-bytes") == 0 ? &max_bytes :
                           strcmp(name, "--max-comparisons") == 0 ? &max_comparisons : NULL;
        if (target == NULL || !parse_u64(argv[position++], target)) { usage(stderr); return 2; }
    }
    if (argc - position != 2 || threads64 < 1 || threads64 > 256) { usage(stderr); return 2; }
    dp_t dp;
    match_t match;
    if (!dp_read(argv[position], &dp)) return 1;
    if (!match_read(argv[position + 1], &dp, &match)) { dp_free(&dp); return 1; }
    uint32_t *elements = NULL, *labels = NULL, *cells = NULL, *rows = NULL;
    unsigned char *psets = NULL, *qsets = NULL;
    bool ok = field_build(&dp, match.polynomial, &elements, &labels, &cells);
    if (!ok) fprintf(stderr, "recorded polynomial is not primitive with generator X\n");
    if (ok) ok = build_partitions(&dp, &match, cells, &psets, &qsets);
    uint64_t row_count = 0;
    if (ok) ok = render_rows(&dp, &match, elements, labels, psets, qsets,
                             max_rows, max_bytes, &rows, &row_count);
    if (ok) ok = rows_are_permutations(rows, row_count, dp.q + 1);
    uint64_t comparisons = 0, possible = 0;
    if (ok) ok = checked_mul_u64(row_count, row_count - 1, &possible) &&
                 (possible /= 2, possible <= max_comparisons);
    if (!ok && rows && possible > max_comparisons)
        fprintf(stderr, "pair comparison exceeds limit: pairs=%" PRIu64 " limit=%" PRIu64 "\n", possible, max_comparisons);
    if (ok) {
        fprintf(stderr, "checking rows=%" PRIu64 " width=%u pairs=%" PRIu64
                " rendered_bytes=%" PRIu64 "\n", row_count, dp.q + 1, possible,
                row_count * ((uint64_t)dp.q + 1) * sizeof(uint32_t));
        if (explain) fprintf(stderr,
            "conventions: label 0 is zero; label k+1 is X^k; coset i uses slope label i+1; "
            "the first covered position is extended; the freebie uses slope label cosets+1\n");
        uint32_t thread_count = (uint32_t)threads64;
        if (thread_count > row_count) thread_count = (uint32_t)row_count;
        compare_task_t *tasks = calloc(thread_count, sizeof *tasks);
        pthread_t *workers = calloc(thread_count, sizeof *workers);
        if (!tasks || !workers) ok = false;
        uint32_t started = 0;
        for (; ok && started < thread_count; ++started) {
            tasks[started] = (compare_task_t){
                .rows=rows, .row_count=row_count, .width=dp.q + 1,
                .thread_index=started, .thread_count=thread_count,
                .minimum=dp.q + 1,
            };
            if (pthread_create(&workers[started], NULL, compare_rows, &tasks[started]) != 0) ok = false;
        }
        for (uint32_t i = 0; i < started; ++i) pthread_join(workers[i], NULL);
        uint32_t minimum = dp.q + 1;
        uint64_t closest_left = 0, closest_right = 0;
        for (uint32_t i = 0; i < started; ++i) {
            comparisons += tasks[i].pairs;
            if (tasks[i].minimum < minimum) {
                minimum = tasks[i].minimum;
                closest_left = tasks[i].closest_left;
                closest_right = tasks[i].closest_right;
            }
            if (tasks[i].failed) {
                fprintf(stderr, "distance failure: rows=%" PRIu64 ",%" PRIu64 " distance=%u required=%u\n",
                        tasks[i].left, tasks[i].right, tasks[i].distance, dp.q);
                ok = false;
            }
        }
        if (ok && render_path != NULL && !render_text(render_path, rows, row_count, dp.q + 1)) {
            fprintf(stderr, "could not publish rendered rows: %s\n", strerror(errno));
            ok = false;
        }
        if (ok) printf("verified p=%u r=%u q=%u classes=%u rows=%" PRIu64
                       " width=%u pairs=%" PRIu64 " minimum_distance=%u closest=%" PRIu64 ",%" PRIu64 "\n",
                       dp.p, dp.r, dp.q, dp.coset_count + 1, row_count, dp.q + 1,
                       comparisons, minimum, closest_left, closest_right);
        free(tasks); free(workers);
    }
    free(rows); free(psets); free(qsets); free(elements); free(labels); free(cells);
    match_free(&match); dp_free(&dp);
    return ok ? 0 : 1;
}
