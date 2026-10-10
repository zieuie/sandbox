/*
 * kh_rehydrate: expand a King Hamming matching certificate (KHM1) and its DP shape (KHD1) into
 * the permutation array it proves exists, and print every piece of metadata either file holds.
 *
 * One file, no dependencies beyond the C library (SHA-256 is built in):
 *     cc -O2 -std=c11 -o kh_rehydrate kh_rehydrate.c
 *
 * Read rehydrate/README.md for the construction, the conventions and the output formats. The
 * construction is Theorem 1 of docs/prime_power_09_26.pdf with the position sets P_i taken from
 * the certificate and the symbol sets Q_i from the DP shape; the conventions are documented in
 * `--explain`. This program shares no code with the solvers or checkers; it follows the same
 * conventions as row_verifier/verify_rows.c, so the two outputs can be compared byte for byte.
 */
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

__extension__ typedef unsigned __int128 u128;

#define MAX_R 63
#define MAX_P 1621
#define NO_POSITION UINT32_MAX

/* ------------------------------------------------------------------------------------------ */
/* SHA-256                                                                                    */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    uint32_t h[8];
    uint64_t length;
    uint8_t block[64];
    size_t used;
} sha_t;

static const uint32_t SHA_K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

static uint32_t rotr32(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

static void sha_init(sha_t *s) {
    static const uint32_t start[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(s->h, start, sizeof start);
    s->length = 0;
    s->used = 0;
}

static void sha_compress(sha_t *s, const uint8_t *b) {
    uint32_t w[64];
    for (int i = 0; i < 16; ++i)
        w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    for (int i = 16; i < 64; ++i) {
        uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = s->h[0], bb = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 64; ++i) {
        uint32_t t1 = h + (rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] + w[i];
        uint32_t t2 = (rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
        h = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += bb; s->h[2] += c; s->h[3] += d;
    s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha_update(sha_t *s, const void *data, size_t size) {
    const uint8_t *p = data;
    s->length += size;
    while (size) {
        size_t take = 64 - s->used < size ? 64 - s->used : size;
        memcpy(s->block + s->used, p, take);
        s->used += take; p += take; size -= take;
        if (s->used == 64) { sha_compress(s, s->block); s->used = 0; }
    }
}

static void sha_final(sha_t *s, uint8_t out[32]) {
    uint64_t bits = s->length * 8;
    uint8_t pad = 0x80;
    sha_update(s, &pad, 1);
    pad = 0;
    while (s->used != 56) sha_update(s, &pad, 1);
    uint8_t tail[8];
    for (int i = 0; i < 8; ++i) tail[i] = (uint8_t)(bits >> (56 - 8 * i));
    sha_update(s, tail, 8);
    for (int i = 0; i < 8; ++i)
        for (int j = 0; j < 4; ++j) out[4 * i + j] = (uint8_t)(s->h[i] >> (24 - 8 * j));
}

static void hex32(const uint8_t d[32], char out[65]) {
    for (int i = 0; i < 32; ++i) sprintf(out + 2 * i, "%02x", d[i]);
}

/* ------------------------------------------------------------------------------------------ */
/* Small helpers                                                                              */
/* ------------------------------------------------------------------------------------------ */

static void print_u128(FILE *f, u128 v) {
    char buf[48];
    int i = 47;
    buf[i] = 0;
    if (v == 0) buf[--i] = '0';
    while (v) { buf[--i] = (char)('0' + (int)(v % 10)); v /= 10; }
    fputs(buf + i, f);
}

static void print_human(FILE *f, u128 bytes) {
    static const char *unit[] = {"B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"};
    double value = (double)bytes;
    int u = 0;
    while (value >= 1024.0 && u < 8) { value /= 1024.0; ++u; }
    fprintf(f, "%.2f %s", value, unit[u]);
}

static bool is_prime(uint64_t n) {
    if (n < 2) return false;
    for (uint64_t d = 2; d * d <= n; ++d)
        if (n % d == 0) return false;
    return true;
}

static bool power_u64(uint64_t base, uint64_t exponent, uint64_t *out) {
    u128 value = 1;
    for (uint64_t i = 0; i < exponent; ++i) {
        value *= base;
        if (value > UINT64_MAX) return false;
    }
    *out = (uint64_t)value;
    return true;
}

static bool read_whole_file(const char *path, uint8_t **bytes, size_t *size, size_t limit) {
    struct stat status;
    *bytes = NULL;
    *size = 0;
    if (stat(path, &status) != 0 || status.st_size < 0 || (uint64_t)status.st_size > limit) return false;
    FILE *file = fopen(path, "rb");
    if (!file) return false;
    *size = (size_t)status.st_size;
    *bytes = malloc(*size ? *size : 1);
    bool ok = *bytes && fread(*bytes, 1, *size, file) == *size && fgetc(file) == EOF;
    fclose(file);
    if (!ok) { free(*bytes); *bytes = NULL; }
    return ok;
}

typedef struct {
    const uint8_t *bytes;
    size_t position, end;
} reader_t;

/* LEB128 varint, canonical encodings only (as the file formats require). */
static bool read_varint(reader_t *r, uint64_t *out) {
    uint64_t value = 0;
    for (unsigned shift = 0; shift < 64; shift += 7) {
        if (r->position >= r->end) return false;
        uint8_t byte = r->bytes[r->position++];
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

/* ------------------------------------------------------------------------------------------ */
/* The DP shape (KHD1)                                                                        */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    uint64_t a, b, t, repeat, omega;
    uint64_t first_coset, first_request, first_special;
} run_t;

typedef struct {
    char path[4096];
    uint64_t file_size;
    uint8_t digest[32];       /* SHA-256 of the whole file */
    uint8_t trailer[32];
    uint64_t p, r, q, f, budget, theta, run_count;
    run_t *runs;
    uint64_t cosets, requests, special, used_a;
    u128 rows;                /* N = q + F^2 theta */
} dp_t;

static bool load_dp(const char *path, dp_t *dp) {
    memset(dp, 0, sizeof *dp);
    snprintf(dp->path, sizeof dp->path, "%s", path);
    uint8_t *bytes;
    size_t size;
    if (!read_whole_file(path, &bytes, &size, 16u << 20) || size < 40 || memcmp(bytes, "KHD1", 4) != 0) {
        fprintf(stderr, "%s: not a readable KHD1 file (tag, or larger than 16 MiB)\n", path);
        free(bytes);
        return false;
    }
    sha_t s;
    uint8_t digest[32];
    sha_init(&s);
    sha_update(&s, bytes, size - 32);
    sha_final(&s, digest);
    if (memcmp(digest, bytes + size - 32, 32) != 0) {
        fprintf(stderr, "%s: KHD1 checksum mismatch\n", path);
        free(bytes);
        return false;
    }
    memcpy(dp->trailer, bytes + size - 32, 32);
    sha_init(&s);
    sha_update(&s, bytes, size);
    sha_final(&s, dp->digest);
    dp->file_size = size;

    reader_t rd = {bytes, 4, size - 32};
    uint64_t p, r, theta, count;
    bool ok = read_varint(&rd, &p) && read_varint(&rd, &r) && read_varint(&rd, &theta) && read_varint(&rd, &count);
    ok = ok && p >= 2 && p <= MAX_P && is_prime(p) && r >= 3 && r <= MAX_R && (r & 1u);
    uint64_t q = 0, f = 0;
    ok = ok && power_u64(p, r, &q) && power_u64(p, r / 2, &f) && p * f <= UINT32_MAX &&
         (u128)(p * f + 1) * (p * f + 1) * 12 <= UINT64_MAX && count >= 1 && count <= p * f;
    if (!ok) {
        fprintf(stderr, "%s: invalid KHD1 dimensions\n", path);
        free(bytes);
        return false;
    }
    dp->p = p; dp->r = r; dp->q = q; dp->f = f; dp->budget = p * f; dp->theta = theta; dp->run_count = count;
    dp->runs = calloc(count, sizeof *dp->runs);
    u128 used_a = 0, used_b = 0, gain = 0, cosets = 0;
    for (uint64_t i = 0; ok && i < count; ++i) {
        run_t run = {0};
        ok = read_varint(&rd, &run.a) && read_varint(&rd, &run.b) && read_varint(&rd, &run.t) &&
             read_varint(&rd, &run.repeat) && run.a >= 1 && run.a <= p && run.b >= 1 && run.b <= p &&
             run.t >= 1 && run.t <= p && run.repeat >= 1 && run.repeat <= dp->budget;
        if (ok && i > 0) {
            const run_t *previous = &dp->runs[i - 1];
            ok = run.a != previous->a || run.b != previous->b || run.t != previous->t;  /* maximal runs */
        }
        if (!ok) break;
        uint8_t seen[MAX_P] = {0};
        uint64_t residues = 0;
        for (uint64_t g = 0; g < run.a; ++g)
            for (uint64_t h = 0; h < run.b; ++h) {
                uint64_t residue = (h * run.t + p - g) % p;
                if (!seen[residue]) { seen[residue] = 1; ++residues; }
            }
        run.omega = residues;
        run.first_coset = (uint64_t)cosets;
        run.first_request = (uint64_t)(used_a * f);
        run.first_special = (uint64_t)used_b;
        u128 copies = (u128)run.t * run.repeat;
        used_a += run.a * copies;
        used_b += run.b * copies;
        gain += copies * residues;
        cosets += copies;
        ok = used_a * f <= UINT64_MAX && cosets <= UINT32_MAX;
        dp->runs[i] = run;
    }
    ok = ok && rd.position == rd.end && used_a <= dp->budget && used_b <= dp->budget && gain == theta &&
         cosets + 1 < q;
    if (!ok) {
        fprintf(stderr, "%s: invalid KHD1 runs or score\n", path);
        free(dp->runs);
        free(bytes);
        memset(dp, 0, sizeof *dp);
        return false;
    }
    dp->cosets = (uint64_t)cosets;
    dp->used_a = (uint64_t)used_a;
    dp->requests = (uint64_t)(used_a * f);
    dp->special = (uint64_t)used_b;
    dp->rows = (u128)q + (u128)f * f * theta;
    free(bytes);
    return true;
}

/* ------------------------------------------------------------------------------------------ */
/* The certificate (KHM1)                                                                     */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    char path[4096];
    FILE *file;
    uint64_t file_size;
    uint8_t dp_digest[32];
    uint8_t trailer[32];
    uint64_t p, r, status, count, matched;
    uint64_t coef[MAX_R + 1];
    uint64_t header_len, bits, packed_bytes;
    bool structure_ok;        /* file size = header + packed choices + 32 */
    bool padding_ok;
} cert_t;

static bool load_cert(const char *path, cert_t *c) {
    memset(c, 0, sizeof *c);
    snprintf(c->path, sizeof c->path, "%s", path);
    c->file = fopen(path, "rb");
    if (!c->file) { fprintf(stderr, "%s: %s\n", path, strerror(errno)); return false; }
    if (fseeko(c->file, 0, SEEK_END) != 0) return false;
    off_t end = ftello(c->file);
    if (end < 0) return false;
    c->file_size = (uint64_t)end;
    uint8_t head[1024];
    size_t got = 0;
    rewind(c->file);
    got = fread(head, 1, sizeof head, c->file);
    if (got < 68 || memcmp(head, "KHM1", 4) != 0) {
        fprintf(stderr, "%s: not a KHM1 certificate\n", path);
        return false;
    }
    memcpy(c->dp_digest, head + 4, 32);
    reader_t rd = {head, 36, got};
    bool ok = read_varint(&rd, &c->p) && read_varint(&rd, &c->r) && c->r >= 3 && c->r <= MAX_R;
    for (uint64_t i = 0; ok && i <= c->r; ++i) ok = read_varint(&rd, &c->coef[i]) && c->coef[i] < c->p;
    ok = ok && read_varint(&rd, &c->status) && read_varint(&rd, &c->count) && read_varint(&rd, &c->matched);
    if (!ok) {
        fprintf(stderr, "%s: truncated or malformed KHM1 header\n", path);
        return false;
    }
    c->header_len = rd.position;
    if (c->file_size < c->header_len + 32 ||
        fseeko(c->file, (off_t)(c->file_size - 32), SEEK_SET) != 0 || fread(c->trailer, 1, 32, c->file) != 32) {
        fprintf(stderr, "%s: cannot read the checksum trailer\n", path);
        return false;
    }
    return true;
}

/* Packed-choice geometry, once F is known from the DP. */
static void cert_geometry(cert_t *c, const dp_t *dp) {
    uint64_t bits = 0;
    while (((uint64_t)1 << bits) < dp->f) ++bits;
    c->bits = bits;
    u128 total_bits = (u128)c->count * bits;
    c->packed_bytes = (uint64_t)((total_bits + 7) / 8);
    c->structure_ok = (u128)c->header_len + c->packed_bytes + 32 == c->file_size;
    c->padding_ok = true;
    unsigned used = (unsigned)(total_bits & 7);
    if (c->structure_ok && c->packed_bytes && used) {
        uint8_t last;
        if (fseeko(c->file, (off_t)(c->file_size - 33), SEEK_SET) == 0 && fread(&last, 1, 1, c->file) == 1)
            c->padding_ok = (last >> used) == 0;
    }
}

static bool cert_checksum(cert_t *c, bool *matches) {
    sha_t s;
    uint8_t digest[32], buffer[1 << 20];
    sha_init(&s);
    if (fseeko(c->file, 0, SEEK_SET) != 0) return false;
    uint64_t left = c->file_size - 32, done = 0;
    while (left) {
        size_t take = left < sizeof buffer ? (size_t)left : sizeof buffer;
        if (fread(buffer, 1, take, c->file) != take) return false;
        sha_update(&s, buffer, take);
        left -= take;
        done += take;
        if ((done & ((1u << 30) - 1)) == 0) fprintf(stderr, "  hashed %" PRIu64 " GiB of the certificate\n", done >> 30);
    }
    sha_final(&s, digest);
    *matches = memcmp(digest, c->trailer, 32) == 0;
    return true;
}

/* Sequential decoder of packed choices from request `start`. */
typedef struct {
    cert_t *cert;
    uint64_t next_index;      /* request index of the next value */
    uint64_t byte_position;   /* file offset of the next unread byte */
    uint64_t accumulator;
    unsigned available;
    uint8_t buffer[1 << 16];
    size_t buffered, used;
} stream_t;

static bool stream_open(stream_t *s, cert_t *cert, uint64_t start) {
    memset(s, 0, offsetof(stream_t, buffer));
    s->cert = cert;
    s->next_index = start;
    u128 bit = (u128)start * cert->bits;
    s->byte_position = cert->header_len + (uint64_t)(bit / 8);
    unsigned skip = (unsigned)(bit % 8);
    if (fseeko(cert->file, (off_t)s->byte_position, SEEK_SET) != 0) return false;
    if (skip) {
        uint8_t byte;
        if (fread(&byte, 1, 1, cert->file) != 1) return false;
        s->byte_position += 1;
        s->accumulator = (uint64_t)(byte >> skip);
        s->available = 8 - skip;
    }
    return true;
}

static bool stream_next(stream_t *s, uint32_t *value) {
    uint64_t bits = s->cert->bits;
    while (s->available < bits) {
        if (s->used == s->buffered) {
            uint64_t limit = s->cert->header_len + s->cert->packed_bytes;
            if (s->byte_position >= limit) return false;
            size_t want = sizeof s->buffer;
            if (limit - s->byte_position < want) want = (size_t)(limit - s->byte_position);
            s->buffered = fread(s->buffer, 1, want, s->cert->file);
            s->used = 0;
            if (s->buffered == 0) return false;
        }
        s->accumulator |= (uint64_t)s->buffer[s->used++] << s->available;
        s->byte_position += 1;
        s->available += 8;
    }
    *value = (uint32_t)(s->accumulator & (((uint64_t)1 << bits) - 1));
    s->accumulator >>= bits;
    s->available -= (unsigned)bits;
    ++s->next_index;
    return true;
}

/* ------------------------------------------------------------------------------------------ */
/* Field arithmetic that needs no tables: primitivity of the polynomial                       */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    uint64_t r, p;
    const uint64_t *coef;     /* f = sum coef[i] X^i, monic of degree r */
} poly_t;

static void poly_mulmod(const poly_t *f, const uint64_t *a, const uint64_t *b, uint64_t *out) {
    uint64_t prod[2 * MAX_R] = {0};
    for (uint64_t i = 0; i < f->r; ++i)
        for (uint64_t j = 0; j < f->r; ++j) prod[i + j] = (prod[i + j] + a[i] * b[j]) % f->p;
    for (uint64_t d = 2 * f->r - 2; d >= f->r; --d) {
        uint64_t lead = prod[d] % f->p;
        prod[d] = 0;
        for (uint64_t j = 0; j < f->r; ++j) {
            uint64_t sub = lead * f->coef[j] % f->p;
            prod[d - f->r + j] = (prod[d - f->r + j] + f->p - sub) % f->p;
        }
        if (d == 0) break;
    }
    for (uint64_t i = 0; i < f->r; ++i) out[i] = prod[i];
}

/* X^e modulo f, by square and multiply. */
static void poly_x_pow(const poly_t *f, uint64_t e, uint64_t *out) {
    uint64_t result[MAX_R] = {0}, base[MAX_R] = {0}, scratch[MAX_R];
    result[0] = 1;
    base[1] = 1;
    while (e) {
        if (e & 1) { poly_mulmod(f, result, base, scratch); memcpy(result, scratch, sizeof result); }
        poly_mulmod(f, base, base, scratch);
        memcpy(base, scratch, sizeof base);
        e >>= 1;
    }
    memcpy(out, result, sizeof result);
}

static bool poly_is_one(const poly_t *f, const uint64_t *v) {
    if (v[0] != 1) return false;
    for (uint64_t i = 1; i < f->r; ++i)
        if (v[i] != 0) return false;
    return true;
}

typedef struct {
    bool monic, constant_nonzero, order_divides, primitive;
    uint64_t primes[64], exponents[64], prime_count;
    bool not_one[64];         /* X^((q-1)/s) != 1 for each prime s */
} primitivity_t;

static void check_primitive(const dp_t *dp, const uint64_t *coef, primitivity_t *out) {
    memset(out, 0, sizeof *out);
    out->monic = coef[dp->r] == 1;
    out->constant_nonzero = coef[0] != 0;
    uint64_t n = dp->q - 1, rest = n;
    for (uint64_t d = 2; d * d <= rest; d += (d == 2 ? 1 : 2)) {
        if (rest % d) continue;
        out->primes[out->prime_count] = d;
        while (rest % d == 0) { rest /= d; ++out->exponents[out->prime_count]; }
        ++out->prime_count;
    }
    if (rest > 1) { out->primes[out->prime_count] = rest; out->exponents[out->prime_count] = 1; ++out->prime_count; }
    if (!out->monic || !out->constant_nonzero) return;
    poly_t f = {dp->r, dp->p, coef};
    uint64_t v[MAX_R];
    poly_x_pow(&f, n, v);
    out->order_divides = poly_is_one(&f, v);
    out->primitive = out->order_divides;
    for (uint64_t i = 0; i < out->prime_count; ++i) {
        poly_x_pow(&f, n / out->primes[i], v);
        out->not_one[i] = !poly_is_one(&f, v);
        out->primitive = out->primitive && out->not_one[i];
    }
}

/* ------------------------------------------------------------------------------------------ */
/* Report                                                                                     */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    bool verify_checksum, checksum_done, checksum_matches;
    bool dp_matches, pair_ok;
    primitivity_t prim;
} facts_t;

static void print_polynomial(FILE *f, const dp_t *dp, const uint64_t *coef) {
    bool first = true;
    for (int64_t i = (int64_t)dp->r; i >= 0; --i) {
        uint64_t c = coef[i];
        if (!c) continue;
        if (!first) fputs(" + ", f);
        first = false;
        if (c != 1 || i == 0) fprintf(f, "%" PRIu64, c);
        if (i >= 1) fprintf(f, "X");
        if (i >= 2) fprintf(f, "^%" PRId64, i);
    }
    fprintf(f, "   (over GF(%" PRIu64 "))", dp->p);
}

static void report(FILE *f, const dp_t *dp, const cert_t *c, const facts_t *x) {
    char hex[65];
    fprintf(f, "== certificate (KHM1) ==\n");
    fprintf(f, "path:                 %s\n", c->path);
    fprintf(f, "size:                 %" PRIu64 " bytes (", c->file_size);
    print_human(f, c->file_size);
    fprintf(f, ")\n");
    fprintf(f, "format tag:           KHM1\n");
    hex32(c->dp_digest, hex);
    fprintf(f, "embedded DP sha256:   %s\n", hex);
    fprintf(f, "  equals the DP file's sha256: %s\n", x->dp_matches ? "yes" : "NO");
    fprintf(f, "p:                    %" PRIu64 "\n", c->p);
    fprintf(f, "r:                    %" PRIu64 "\n", c->r);
    fprintf(f, "primitive polynomial: coefficients, lowest degree first: ");
    for (uint64_t i = 0; i <= c->r; ++i) fprintf(f, "%s%" PRIu64, i ? "," : "", c->coef[i]);
    fprintf(f, "\n                      f(X) = ");
    print_polynomial(f, dp, c->coef);
    fprintf(f, "\n                      X^%" PRIu64 " = -(", c->r);
    {
        bool first = true;
        for (uint64_t i = 0; i < c->r; ++i)
            if (c->coef[i]) { fprintf(f, "%s%" PRIu64 "*X^%" PRIu64, first ? "" : " + ", c->coef[i], i); first = false; }
        fprintf(f, ")  in GF(%" PRIu64 ")[X]/f   (the reduction rule)\n", c->p);
    }
    fprintf(f, "outcome (status):     %" PRIu64 "  (%s)\n", c->status,
            c->status == 0 ? "0 = full matching" : c->status == 1 ? "1 = Hall witness: no matching" : "unknown");
    fprintf(f, "requests n:           %" PRIu64 "\n", c->count);
    fprintf(f, "matched:              %" PRIu64 "%s\n", c->matched, c->matched == c->count ? " (all requests)" : "");
    fprintf(f, "bits per choice:      %" PRIu64 "  (ceil(log2 F), F = %" PRIu64 ")\n", c->bits, dp->f);
    fprintf(f, "header bytes:         %" PRIu64 "\n", c->header_len);
    fprintf(f, "packed choice bytes:  %" PRIu64 "\n", c->packed_bytes);
    fprintf(f, "size = header + choices + 32: %s\n", c->structure_ok ? "yes" : "NO");
    fprintf(f, "padding bits zero:    %s\n", c->padding_ok ? "yes" : "NO");
    hex32(c->trailer, hex);
    fprintf(f, "trailer sha256:       %s\n", hex);
    if (x->checksum_done)
        fprintf(f, "  equals sha256 of the rest of the file: %s\n", x->checksum_matches ? "yes" : "NO");
    else
        fprintf(f, "  equals sha256 of the rest of the file: not checked (--verify-checksum hashes the whole file)\n");

    fprintf(f, "\n== DP shape (KHD1) ==\n");
    fprintf(f, "path:                 %s\n", dp->path);
    fprintf(f, "size:                 %" PRIu64 " bytes\n", dp->file_size);
    hex32(dp->digest, hex);
    fprintf(f, "sha256 (whole file):  %s\n", hex);
    hex32(dp->trailer, hex);
    fprintf(f, "trailer sha256:       %s  (verified)\n", hex);
    fprintf(f, "p, r:                 %" PRIu64 ", %" PRIu64 "\n", dp->p, dp->r);
    fprintf(f, "q = p^r:              %" PRIu64 "\n", dp->q);
    fprintf(f, "F = p^floor(r/2):     %" PRIu64 "\n", dp->f);
    fprintf(f, "budget p*F:           %" PRIu64 "\n", dp->budget);
    fprintf(f, "theta:                %" PRIu64 "\n", dp->theta);
    fprintf(f, "runs:                 %" PRIu64 "\n", dp->run_count);
    fprintf(f, "cosets:               %" PRIu64 "  (indices 0..%" PRIu64 "; class %" PRIu64 " is the freebie)\n",
            dp->cosets, dp->cosets - 1, dp->cosets);
    fprintf(f, "requests n = F * sum(a*t*repeat) = F * %" PRIu64 " = %" PRIu64 "\n", dp->used_a, dp->requests);
    fprintf(f, "special sets used:    %" PRIu64 "  (of %" PRIu64 ")\n", dp->special, dp->budget);
    fprintf(f, "request count matches the certificate: %s\n", dp->requests == c->count ? "yes" : "NO");

    fprintf(f, "\n== the claim ==\n");
    fprintf(f, "N = q + F^2 * theta = ");
    print_u128(f, dp->rows);
    fprintf(f, " permutations\n");
    fprintf(f, "each is a permutation of q+1 = %" PRIu64 " symbols; any two disagree in at least q = %" PRIu64
            " positions\n", dp->q + 1, dp->q);
    fprintf(f, "i.e. M(%" PRIu64 ", %" PRIu64 ") >= ", dp->q + 1, dp->q);
    print_u128(f, dp->rows);
    fprintf(f, "\n");

    fprintf(f, "\n== the field ==\n");
    fprintf(f, "monic: %s;  constant term nonzero: %s\n", x->prim.monic ? "yes" : "NO",
            x->prim.constant_nonzero ? "yes" : "NO");
    fprintf(f, "q - 1 = ");
    for (uint64_t i = 0; i < x->prim.prime_count; ++i) {
        fprintf(f, "%s%" PRIu64, i ? " * " : "", x->prim.primes[i]);
        if (x->prim.exponents[i] > 1) fprintf(f, "^%" PRIu64, x->prim.exponents[i]);
    }
    fprintf(f, "  = %" PRIu64 "\n", dp->q - 1);
    fprintf(f, "X^(q-1) = 1 in GF(p)[X]/f: %s\n", x->prim.order_divides ? "yes" : "NO");
    for (uint64_t i = 0; i < x->prim.prime_count && x->prim.order_divides; ++i)
        fprintf(f, "X^((q-1)/%" PRIu64 ") != 1: %s\n", x->prim.primes[i], x->prim.not_one[i] ? "yes" : "NO");
    fprintf(f, "=> f is primitive (X generates all q-1 nonzero elements): %s\n", x->prim.primitive ? "YES" : "NO");

    fprintf(f, "\n== DP runs (class structure) ==\n");
    fprintf(f, "%5s %6s %6s %6s %9s %5s %12s %14s %16s %16s %14s\n", "run", "a", "b", "t", "repeat", "omega",
            "first coset", "cosets", "first request", "requests", "rows/coset");
    for (uint64_t i = 0; i < dp->run_count; ++i) {
        const run_t *r = &dp->runs[i];
        if (dp->run_count > 40 && i == 20) {
            fprintf(f, "  ... %" PRIu64 " more runs (all are used when rendering) ...\n", dp->run_count - 40);
            i = dp->run_count - 21;
            continue;
        }
        fprintf(f, "%5" PRIu64 " %6" PRIu64 " %6" PRIu64 " %6" PRIu64 " %9" PRIu64 " %5" PRIu64 " %12" PRIu64 " %14" PRIu64
                " %16" PRIu64 " %16" PRIu64 " ", i, r->a, r->b, r->t, r->repeat, r->omega, r->first_coset,
                r->t * r->repeat, r->first_request, r->a * r->t * r->repeat * dp->f);
        print_u128(f, (u128)dp->f * dp->f * r->omega);
        fprintf(f, "\n");
    }
    fprintf(f, "(omega = number of distinct residues (h*t - g) mod p for g<a, h<b; each coset of the run keeps\n"
               " F^2*omega rows, and theta = sum over runs of t*repeat*omega.)\n");

    fprintf(f, "\n== the whole array, if it were written out ==\n");
    u128 symbols = dp->rows * (dp->q + 1);
    fprintf(f, "rows x width = ");
    print_u128(f, dp->rows);
    fprintf(f, " x %" PRIu64 " = ", dp->q + 1);
    print_u128(f, symbols);
    fprintf(f, " symbols\n");
    unsigned w = dp->q <= 255 ? 1 : dp->q <= 65535 ? 2 : dp->q <= 0xFFFFFFFFu ? 4 : 8;
    fprintf(f, "as binary at %u byte(s) per symbol: ", w);
    print_human(f, symbols * w);
    fprintf(f, "\n(--classes A:B renders only some classes; the output is streamed, so a part is cheap.)\n");
}

static void explain(FILE *f) {
    fputs(
        "\n== conventions used by the rendering ==\n"
        "field     GF(p)[X]/f, f the recorded primitive polynomial; an element is r coefficients c_0..c_(r-1),\n"
        "          packed value = sum c_i p^i; reduction X^r = -(f_0 + f_1 X + ... + f_(r-1) X^(r-1)).\n"
        "labels    label 0 = the zero element; label k+1 = X^k. Symbols 0..q-1 are labels, symbol q is the\n"
        "          extension symbol (the paper's star). Positions 0..q-1 are labels too; position q is the new\n"
        "          last position.\n"
        "rows      coset i, translate b (a label 0..q-1) is the row x -> w*x + b with w = X^i (label i+1):\n"
        "          row[pos] = label( w*elem(pos) + elem(b) ) for pos = 0..q-1.\n"
        "cells     element e has suffix = e mod F (its low floor(r/2) coefficients) and prefix residue = sum of\n"
        "          its other coefficients mod p. Cell (g, beta) = the F elements with prefix g, suffix beta,\n"
        "          listed by increasing label.\n"
        "requests  (coset i, stripe g < a, suffix beta < F), in that order, coset after coset, with choice k:\n"
        "          label L = k-th label of cell (g, beta); position = L if L = 0, else 1 + ((L-1) + (q-1) - i)\n"
        "          mod (q-1). P_i is the set of these positions.\n"
        "special   sets are numbered j = 0,1,2,... through the DP runs (b*t per copy of a run); within a copy\n"
        "          the k-th goes to its coset k mod t. Special set j is the cell with prefix j mod p and\n"
        "          suffix j div p; Q_i is the union of the cells of coset i's special sets.\n"
        "extend    a row of coset i is kept iff some position x in P_i holds a symbol in Q_i. At the smallest\n"
        "          such x the symbol there moves to the new last position q and x gets the star. Other rows\n"
        "          are dropped.\n"
        "freebie   class number = cosets, w = X^cosets: every translate is kept and the star is appended.\n"
        "order     cosets in order, rows by increasing b, then the freebie class.\n", f);
}

/* ------------------------------------------------------------------------------------------ */
/* Rendering                                                                                  */
/* ------------------------------------------------------------------------------------------ */

typedef struct {
    const char *output;       /* file, or "-" for stdout */
    const char *meta;
    const char *requests;
    bool text;                /* else binary */
    bool verify_checksum, explain, classes_set;
    uint64_t c0, c1, max_bytes, max_memory;
} opts_t;

typedef struct {
    bool attempted, ok, full;
    uint64_t c0, c1;
    u128 rows;
    uint64_t bytes, freebie_rows, occupied;
    uint8_t sha[32];
    uint64_t *run_min, *run_max;   /* observed rows kept per coset, per run (UINT64_MAX / 0 if no coset) */
    char error[256];
} render_t;

static inline void bs_set(uint8_t *b, uint64_t i) { b[i >> 3] |= (uint8_t)(1u << (i & 7)); }
static inline void bs_clear(uint8_t *b, uint64_t i) { b[i >> 3] &= (uint8_t)~(1u << (i & 7)); }
static inline bool bs_get(const uint8_t *b, uint64_t i) { return (b[i >> 3] >> (i & 7)) & 1; }

typedef struct {
    FILE *file;
    bool text, failed;
    unsigned width;
    sha_t sha;
    uint64_t bytes;
    uint8_t *buffer;
    size_t used;
} writer_t;

#define WRITER_CAPACITY (1u << 22)

static void writer_flush(writer_t *w) {
    if (w->used && fwrite(w->buffer, 1, w->used, w->file) != w->used) w->failed = true;
    sha_update(&w->sha, w->buffer, w->used);
    w->bytes += w->used;
    w->used = 0;
}

static void writer_row(writer_t *w, const uint32_t *row, uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) {
        if (w->used + 16 > WRITER_CAPACITY) writer_flush(w);
        uint8_t *out = w->buffer + w->used;
        if (w->text) {
            char digits[12];
            int count = 0;
            uint32_t v = row[i];
            do { digits[count++] = (char)('0' + v % 10); v /= 10; } while (v);
            while (count) *out++ = (uint8_t)digits[--count];
            *out++ = i + 1 == n ? '\n' : ' ';
        } else {
            for (unsigned b = 0; b < w->width; ++b) *out++ = (uint8_t)(row[i] >> (8 * b));
        }
        w->used = (size_t)(out - w->buffer);
    }
}

static size_t find_run(const dp_t *dp, uint64_t coset) {
    size_t lo = 0, hi = dp->run_count;
    while (lo + 1 < hi) {
        size_t mid = (lo + hi) / 2;
        if (dp->runs[mid].first_coset <= coset) lo = mid; else hi = mid;
    }
    return lo;
}

static int compare_u32(const void *x, const void *y) {
    uint32_t a = *(const uint32_t *)x, b = *(const uint32_t *)y;
    return (a > b) - (a < b);
}

static bool render_fail(render_t *rep, const char *format, ...) __attribute__((format(printf, 2, 3)));
static bool render_fail(render_t *rep, const char *format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(rep->error, sizeof rep->error, format, args);
    va_end(args);
    return false;
}

static unsigned decimal_digits(uint64_t v) {
    unsigned n = 1;
    while (v >= 10) { v /= 10; ++n; }
    return n;
}

static bool render(const dp_t *dp, cert_t *cert, const opts_t *o, render_t *rep, FILE *requests) {
    rep->attempted = true;
    if (dp->q >= 0xFFFFFFFEu)
        return render_fail(rep, "q = %" PRIu64 " is too large to render rows (tables need q below 2^32); "
                           "use the metadata, or a smaller field", dp->q);
    const uint32_t p = (uint32_t)dp->p, r = (uint32_t)dp->r, q = (uint32_t)dp->q, f = (uint32_t)dp->f;
    const uint64_t classes = dp->cosets + 1;
    uint64_t c0 = o->classes_set ? o->c0 : 0, c1 = o->classes_set ? o->c1 : classes;
    if (c0 >= c1 || c1 > classes)
        return render_fail(rep, "--classes %" PRIu64 ":%" PRIu64 " is outside 0:%" PRIu64, c0, c1, classes);
    rep->c0 = c0; rep->c1 = c1;
    rep->full = c0 == 0 && c1 == classes;

    /* Size guards before any big allocation or output. */
    u128 estimate_rows = 0;
    for (uint64_t c = c0; c < c1; ++c)
        estimate_rows += c == dp->cosets ? q : (u128)f * f * dp->runs[find_run(dp, c)].omega;
    u128 symbol_bytes = o->text ? decimal_digits(q) + 1 : (q <= 255 ? 1 : q <= 65535 ? 2 : 4);
    u128 estimate_bytes = estimate_rows * (q + 1) * symbol_bytes;
    if (estimate_bytes > o->max_bytes) {
        char text[64];
        if (estimate_bytes >= (u128)1 << 30)
            snprintf(text, sizeof text, "%.1f GiB", (double)estimate_bytes / (1024.0 * 1024 * 1024));
        else
            snprintf(text, sizeof text, "%" PRIu64 " bytes", (uint64_t)estimate_bytes);
        return render_fail(rep, "this would write about %s (limit %" PRIu64 " bytes); raise --max-bytes or "
                           "choose --classes A:B", text, o->max_bytes);
    }
    u128 memory = (u128)q * (4 + 4 + 2 * r) + (u128)q / 2 + 8ull * q + 4ull * (q + 1) + 4ull * dp->budget + 4 * classes;
    if (memory > o->max_memory)
        return render_fail(rep, "the tables need about %" PRIu64 " bytes of memory (limit %" PRIu64 "); raise --max-memory",
                           (uint64_t)memory, o->max_memory);

    uint32_t *labels = malloc((size_t)q * sizeof *labels);
    uint32_t *cells = malloc((size_t)q * sizeof *cells);
    uint32_t *fill = calloc(dp->budget, sizeof *fill);
    uint16_t *ed = malloc((size_t)q * r * sizeof *ed);
    uint8_t *occupied = calloc(((size_t)q + 7) / 8, 1), *pbits = calloc(((size_t)q + 7) / 8, 1),
            *qbits = calloc(((size_t)q + 7) / 8, 1), *seen = malloc((size_t)q + 1);
    uint32_t *row = malloc(((size_t)q + 1) * sizeof *row), *plist = malloc(((size_t)q + 1) * sizeof *plist),
             *qlist = malloc(((size_t)q + 1) * sizeof *qlist);
    uint32_t *kept = calloc(classes, sizeof *kept);
    writer_t w = {0};
    bool ok = labels && cells && fill && ed && occupied && pbits && qbits && seen && row && plist && qlist && kept;
    if (!ok) { render_fail(rep, "out of memory"); goto done; }
    rep->run_min = malloc(dp->run_count * sizeof *rep->run_min);
    rep->run_max = calloc(dp->run_count, sizeof *rep->run_max);
    for (uint64_t i = 0; i < dp->run_count; ++i) rep->run_min[i] = UINT64_MAX;

    /* The field: labels, digit vectors and cells, from the polynomial alone. */
    uint32_t pw[MAX_R + 1];
    pw[0] = 1;
    for (uint32_t i = 1; i < r; ++i) pw[i] = pw[i - 1] * p;
    memset(labels, 0xff, (size_t)q * sizeof *labels);
    {
        uint16_t cur[MAX_R] = {0};
        cur[0] = 1;
        memset(ed, 0, r * sizeof *ed);          /* label 0 = the zero element */
        labels[0] = 0;
        fill[0] = 1;                            /* cell (0,0) starts with label 0 */
        cells[0] = 0;
        for (uint32_t label = 1; label < q; ++label) {
            uint32_t element = 0, suffix = 0, prefix = 0;
            for (uint32_t i = 0; i < r; ++i) {
                element += cur[i] * pw[i];
                if (i < r / 2) suffix += cur[i] * pw[i]; else prefix += cur[i];
                ed[(size_t)label * r + i] = cur[i];
            }
            if (labels[element] != UINT32_MAX) { ok = render_fail(rep, "X has order %u < q-1: the polynomial is not primitive", label - 1); goto done; }
            labels[element] = label;
            uint64_t cell = (uint64_t)(prefix % p) * f + suffix;
            if (fill[cell] >= f) { ok = render_fail(rep, "a cell has more than F elements"); goto done; }
            cells[cell * f + fill[cell]++] = label;
            /* cur = cur * X */
            uint16_t top = cur[r - 1];
            for (uint32_t i = r - 1; i > 0; --i) cur[i] = cur[i - 1];
            cur[0] = 0;
            if (top)
                for (uint32_t j = 0; j < r; ++j) cur[j] = (uint16_t)((cur[j] + p - (uint32_t)top * cert->coef[j] % p) % p);
        }
        bool one = cur[0] == 1;
        for (uint32_t i = 1; i < r; ++i) one = one && cur[i] == 0;
        if (!one) { ok = render_fail(rep, "X^(q-1) != 1: the polynomial is not primitive"); goto done; }
        for (uint64_t i = 0; i < dp->budget; ++i)
            if (fill[i] != f) { ok = render_fail(rep, "cell %" PRIu64 " has %u elements, not F", i, fill[i]); goto done; }
    }

    /* Output. */
    if (o->output) {
        bool to_stdout = strcmp(o->output, "-") == 0;
        if (to_stdout) w.file = stdout;
        else {
            int fd = open(o->output, O_WRONLY | O_CREAT | O_EXCL, 0644);
            if (fd < 0) { ok = render_fail(rep, "cannot create %s: %s (it must not exist)", o->output, strerror(errno)); goto done; }
            w.file = fdopen(fd, "wb");
        }
        w.text = o->text;
        w.width = q <= 255 ? 1 : q <= 65535 ? 2 : 4;
        w.buffer = malloc(WRITER_CAPACITY);
        sha_init(&w.sha);
        if (!w.file || !w.buffer) { ok = render_fail(rep, "cannot open the output"); goto done; }
    }

    stream_t stream;
    bool streaming = false;
    u128 rows = 0;
    for (uint64_t c = c0; c < c1; ++c) {
        uint32_t kept_here = 0;
        if (c < dp->cosets) {
            size_t ri = find_run(dp, c);
            const run_t *run = &dp->runs[ri];
            uint64_t offset = c - run->first_coset, block = offset % run->t, copy = offset / run->t;
            uint64_t start = run->first_request + offset * run->a * f;
            if (!streaming) {
                if (!stream_open(&stream, cert, start)) { ok = render_fail(rep, "cannot read the certificate's choices"); goto done; }
                streaming = true;
            }
            /* P_c: the positions the certificate assigns to this coset's requests. */
            uint32_t np = 0, nq = 0;
            for (uint64_t g = 0; g < run->a; ++g)
                for (uint32_t beta = 0; beta < f; ++beta) {
                    uint32_t k;
                    if (!stream_next(&stream, &k)) { ok = render_fail(rep, "certificate ends early at request %" PRIu64, stream.next_index); goto done; }
                    if (k >= f) { ok = render_fail(rep, "choice %u at request %" PRIu64 " is not below F = %u", k, stream.next_index - 1, f); goto done; }
                    uint32_t label = cells[((uint64_t)g * f + beta) * f + k];
                    uint32_t right = label == 0 ? 0 : (uint32_t)(1 + ((uint64_t)(label - 1) + (q - 1) - c) % (q - 1));
                    if (bs_get(occupied, right)) {
                        ok = render_fail(rep, "the matching uses position %u twice (second time at request %" PRIu64 ", coset %" PRIu64 ")", right, stream.next_index - 1, c);
                        goto done;
                    }
                    bs_set(occupied, right);
                    ++rep->occupied;
                    bs_set(pbits, right);
                    plist[np++] = right;
                    if (requests) {
                        fprintf(requests, "%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%u\t%u\t%u\t", stream.next_index - 1, c, g, beta, k, label);
                        if (label == 0) fputs("zero", requests); else fprintf(requests, "X^%u", label - 1);
                        fputc('\t', requests);
                        for (uint32_t i = 0; i < r; ++i) fprintf(requests, "%s%u", i ? "," : "", ed[(size_t)label * r + i]);
                        fprintf(requests, "\t%" PRIu64 "\t%u\n", (uint64_t)g * f + beta, right);
                    }
                }
            /* Q_c: the cells of this coset's special sets. */
            uint64_t j0 = run->first_special + copy * run->b * run->t;
            for (uint64_t m = 0; m < run->b; ++m) {
                uint64_t j = j0 + block + run->t * m;
                uint64_t cell = (j % p) * f + j / p;
                for (uint32_t x = 0; x < f; ++x) {
                    uint32_t label = cells[cell * f + x];
                    bs_set(qbits, label);
                    qlist[nq++] = label;
                }
            }
            qsort(plist, np, sizeof *plist, compare_u32);
            for (uint32_t tr = 0; tr < q; ++tr) {
                const uint16_t *td = &ed[(size_t)tr * r];
                uint32_t covered = NO_POSITION;
                for (uint32_t i = 0; i < np && covered == NO_POSITION; ++i) {
                    uint32_t pos = plist[i];
                    uint64_t product = pos == 0 ? 0 : (c + pos - 1) % (q - 1) + 1;
                    const uint16_t *xd = &ed[(size_t)product * r];
                    uint32_t sum = 0;
                    for (uint32_t d = 0; d < r; ++d) {
                        uint32_t s = xd[d] + td[d];
                        if (s >= p) s -= p;
                        sum += s * pw[d];
                    }
                    if (bs_get(qbits, labels[sum])) covered = pos;
                }
                if (covered == NO_POSITION) continue;
                for (uint32_t pos = 0; pos < q; ++pos) {
                    uint64_t product = pos == 0 ? 0 : (c + pos - 1) % (q - 1) + 1;
                    const uint16_t *xd = &ed[(size_t)product * r];
                    uint32_t sum = 0;
                    for (uint32_t d = 0; d < r; ++d) {
                        uint32_t s = xd[d] + td[d];
                        if (s >= p) s -= p;
                        sum += s * pw[d];
                    }
                    row[pos] = labels[sum];
                }
                row[q] = row[covered];
                row[covered] = q;
                memset(seen, 0, (size_t)q + 1);
                for (uint32_t pos = 0; pos <= q; ++pos) {
                    if (row[pos] > q || seen[row[pos]]) { ok = render_fail(rep, "internal error: row (coset %" PRIu64 ", translate %u) is not a permutation", c, tr); goto done; }
                    seen[row[pos]] = 1;
                }
                ++kept_here;
                ++rows;
                if (o->output) {
                    writer_row(&w, row, q + 1);
                    if (w.failed || w.bytes + w.used > o->max_bytes) { ok = render_fail(rep, w.failed ? "write error" : "output exceeded --max-bytes"); goto done; }
                }
            }
            for (uint32_t i = 0; i < np; ++i) bs_clear(pbits, plist[i]);
            for (uint32_t i = 0; i < nq; ++i) bs_clear(qbits, qlist[i]);
            kept[c] = kept_here;
            if (kept_here < rep->run_min[ri]) rep->run_min[ri] = kept_here;
            if (kept_here > rep->run_max[ri]) rep->run_max[ri] = kept_here;
        } else {
            /* The freebie class: w = X^cosets, every translate kept, the star appended. */
            for (uint32_t tr = 0; tr < q; ++tr) {
                const uint16_t *td = &ed[(size_t)tr * r];
                for (uint32_t pos = 0; pos < q; ++pos) {
                    uint64_t product = pos == 0 ? 0 : (c + pos - 1) % (q - 1) + 1;
                    const uint16_t *xd = &ed[(size_t)product * r];
                    uint32_t sum = 0;
                    for (uint32_t d = 0; d < r; ++d) {
                        uint32_t s = xd[d] + td[d];
                        if (s >= p) s -= p;
                        sum += s * pw[d];
                    }
                    row[pos] = labels[sum];
                }
                row[q] = q;
                memset(seen, 0, (size_t)q + 1);
                for (uint32_t pos = 0; pos <= q; ++pos) {
                    if (row[pos] > q || seen[row[pos]]) { ok = render_fail(rep, "internal error: freebie row %u is not a permutation", tr); goto done; }
                    seen[row[pos]] = 1;
                }
                ++rows;
                ++rep->freebie_rows;
                if (o->output) {
                    writer_row(&w, row, q + 1);
                    if (w.failed || w.bytes + w.used > o->max_bytes) { ok = render_fail(rep, w.failed ? "write error" : "output exceeded --max-bytes"); goto done; }
                }
            }
        }
    }
    rep->rows = rows;
    if (o->output) {
        writer_flush(&w);
        sha_final(&w.sha, rep->sha);
        rep->bytes = w.bytes;
        if (w.failed || fflush(w.file) != 0 || (w.file != stdout && (fsync(fileno(w.file)) != 0 || fclose(w.file) != 0))) {
            ok = render_fail(rep, "write error");
        }
        w.file = NULL;
    }
done:
    if (!ok && o->output && strcmp(o->output, "-") != 0) {
        if (w.file) fclose(w.file);
        unlink(o->output);
        w.file = NULL;
    }
    if (ok) rep->ok = true;
    free(labels); free(cells); free(fill); free(ed); free(occupied); free(pbits); free(qbits); free(seen);
    free(row); free(plist); free(qlist); free(kept); free(w.buffer);
    return ok;
}

static void report_render(FILE *f, const dp_t *dp, const cert_t *c, const render_t *rep, const opts_t *o) {
    (void)c;
    fprintf(f, "\n== rendering ==\n");
    if (!rep->attempted) { fprintf(f, "not requested (give --output PATH)\n"); return; }
    if (!rep->ok) { fprintf(f, "FAILED: %s\n", rep->error); return; }
    fprintf(f, "classes rendered:     %" PRIu64 " to %" PRIu64 " (of 0 to %" PRIu64 "; class %" PRIu64 " is the freebie)\n",
            rep->c0, rep->c1 - 1, dp->cosets, dp->cosets);
    fprintf(f, "rows written:         ");
    print_u128(f, rep->rows);
    fprintf(f, "  (row width %" PRIu64 ")\n", dp->q + 1);
    fprintf(f, "freebie rows:         %" PRIu64 "\n", rep->freebie_rows);
    if (o->output) {
        char hex[65];
        hex32(rep->sha, hex);
        fprintf(f, "output:               %s\n", strcmp(o->output, "-") == 0 ? "(stdout)" : o->output);
        fprintf(f, "output format:        %s\n", o->text ? "text: one row per line, symbols separated by single spaces"
                : (dp->q <= 255 ? "binary: 1 byte per symbol, little endian, row major, no header"
                   : dp->q <= 65535 ? "binary: 2 bytes per symbol, little endian, row major, no header"
                   : "binary: 4 bytes per symbol, little endian, row major, no header"));
        fprintf(f, "output bytes:         %" PRIu64 "\n", rep->bytes);
        fprintf(f, "output sha256:        %s\n", hex);
    }
    fprintf(f, "every row is a permutation of 0..%" PRIu64 " (checked as written)\n", dp->q);
    fprintf(f, "matching positions seen so far: %" PRIu64 ", none twice\n", rep->occupied);
    fprintf(f, "\nrows kept per coset, by run (observed) against F^2*omega (from the DP):\n");
    fprintf(f, "%5s %14s %14s %14s %s\n", "run", "min", "max", "F^2*omega", "agree");
    for (uint64_t i = 0; i < dp->run_count; ++i) {
        if (rep->run_min[i] == UINT64_MAX) continue;
        u128 expect = (u128)dp->f * dp->f * dp->runs[i].omega;
        fprintf(f, "%5" PRIu64 " %14" PRIu64 " %14" PRIu64 " ", i, rep->run_min[i], rep->run_max[i]);
        print_u128(f, expect);
        fprintf(f, " %s\n", rep->run_min[i] == rep->run_max[i] && expect == rep->run_min[i] ? "yes" : "NO");
    }
    if (rep->full) {
        bool rows_ok = rep->rows == dp->rows, used_ok = rep->occupied == dp->requests;
        fprintf(f, "\nwhole array: rows written = N = q + F^2*theta: %s\n", rows_ok ? "yes" : "NO");
        fprintf(f, "whole array: every one of the %" PRIu64 " matching positions used exactly once: %s\n",
                dp->requests, used_ok ? "yes" : "NO");
    } else {
        fprintf(f, "\npartial render: the whole-array checks (row total, global uniqueness of the matching) need "
                   "all classes and were not made\n");
    }
}

/* ------------------------------------------------------------------------------------------ */
/* Locating files, options, main                                                              */
/* ------------------------------------------------------------------------------------------ */

static bool find_files(const char *state, uint64_t p, uint64_t r, char *dp_path, char *cert_path) {
    char pattern[4096];
    glob_t certs = {0}, dps = {0};
    snprintf(pattern, sizeof pattern, "%s/matching-results/%" PRIu64 "_%" PRIu64 "_*.khmatch", state, p, r);
    glob(pattern, 0, NULL, &certs);
    snprintf(pattern, sizeof pattern, "%s/results/%" PRIu64 "_%" PRIu64 "_*.khmatch", state, p, r);
    glob(pattern, GLOB_APPEND, NULL, &certs);
    snprintf(pattern, sizeof pattern, "%s/results/%" PRIu64 "_%" PRIu64 "_*.khdp", state, p, r);
    glob(pattern, 0, NULL, &dps);
    bool found = false;
    for (size_t i = 0; i < certs.gl_pathc && !found; ++i) {
        cert_t c;
        if (!load_cert(certs.gl_pathv[i], &c)) continue;
        bool full = c.status == 0;
        fclose(c.file);
        for (size_t j = 0; full && j < dps.gl_pathc && !found; ++j) {
            uint8_t *bytes;
            size_t size;
            if (!read_whole_file(dps.gl_pathv[j], &bytes, &size, 16u << 20)) continue;
            sha_t s;
            uint8_t digest[32];
            sha_init(&s);
            sha_update(&s, bytes, size);
            sha_final(&s, digest);
            free(bytes);
            if (memcmp(digest, c.dp_digest, 32) == 0) {
                snprintf(cert_path, 4096, "%s", certs.gl_pathv[i]);
                snprintf(dp_path, 4096, "%s", dps.gl_pathv[j]);
                found = true;
            }
        }
    }
    globfree(&certs);
    globfree(&dps);
    return found;
}

static void usage(FILE *f) {
    fputs(
        "usage: kh_rehydrate [options] DP.khdp MATCH.khmatch\n"
        "       kh_rehydrate [options] --state DEPLOYMENT_DIR --field P,R\n"
        "\n"
        "Prints every piece of metadata in a matching certificate and its DP shape. With --output it\n"
        "also writes the permutation array the certificate proves exists (or part of it).\n"
        "\n"
        "  --state DIR --field P,R   find the full-matching certificate for p^r in DIR/matching-results\n"
        "                            (or DIR/results) and the DP file it belongs to, by hash\n"
        "  --output PATH|-           write the rows here (PATH must not exist; - is stdout)\n"
        "  --format text|bin         text: one row per line (default); bin: 1, 2 or 4 bytes per symbol\n"
        "                            (smallest that holds q), little endian, row major, no header\n"
        "  --classes A:B             render only classes A..B-1 (cosets 0..cosets-1, then the freebie\n"
        "                            class = cosets); default all\n"
        "  --requests PATH           write one tab-separated line per certificate request (for the classes\n"
        "                            rendered): index class stripe suffix choice label exponent coeffs cell position\n"
        "  --meta PATH               also save the report (default: PATH.meta.txt next to --output)\n"
        "  --verify-checksum         hash the whole certificate against its trailer (reads every byte)\n"
        "  --max-bytes N             refuse to write more than N bytes (default 2 GiB)\n"
        "  --max-memory N            refuse tables larger than N bytes (default 8 GiB)\n"
        "  --explain                 print the construction's conventions\n"
        "  --help\n"
        "\n"
        "Exit status 0 if everything checked is consistent, 1 if not, 2 for a usage error.\n", f);
}

static bool parse_u64(const char *text, uint64_t *out) {
    char *end;
    errno = 0;
    unsigned long long v = strtoull(text, &end, 10);
    if (errno || end == text || *end) return false;
    *out = v;
    return true;
}

int main(int argc, char **argv) {
    opts_t o = {0};
    o.text = true;
    o.max_bytes = (uint64_t)2 << 30;
    o.max_memory = (uint64_t)8 << 30;
    const char *state = NULL, *field = NULL, *positional[2] = {NULL, NULL};
    int npos = 0;
    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i];
        if (strncmp(a, "--", 2) != 0) {
            if (npos == 2) { usage(stderr); return 2; }
            positional[npos++] = a;
            continue;
        }
        if (!strcmp(a, "--help")) { usage(stdout); return 0; }
        if (!strcmp(a, "--explain")) { o.explain = true; continue; }
        if (!strcmp(a, "--verify-checksum")) { o.verify_checksum = true; continue; }
        if (i + 1 >= argc) { usage(stderr); return 2; }
        const char *v = argv[++i];
        if (!strcmp(a, "--state")) state = v;
        else if (!strcmp(a, "--field")) field = v;
        else if (!strcmp(a, "--output")) o.output = v;
        else if (!strcmp(a, "--meta")) o.meta = v;
        else if (!strcmp(a, "--requests")) o.requests = v;
        else if (!strcmp(a, "--format")) {
            if (!strcmp(v, "text")) o.text = true; else if (!strcmp(v, "bin")) o.text = false;
            else { usage(stderr); return 2; }
        } else if (!strcmp(a, "--classes")) {
            const char *colon = strchr(v, ':');
            char left[32] = {0};
            if (!colon || colon - v >= 31) { usage(stderr); return 2; }
            memcpy(left, v, (size_t)(colon - v));
            if (!parse_u64(left, &o.c0) || !parse_u64(colon + 1, &o.c1)) { usage(stderr); return 2; }
            o.classes_set = true;
        } else if (!strcmp(a, "--max-bytes")) { if (!parse_u64(v, &o.max_bytes)) { usage(stderr); return 2; } }
        else if (!strcmp(a, "--max-memory")) { if (!parse_u64(v, &o.max_memory)) { usage(stderr); return 2; } }
        else { usage(stderr); return 2; }
    }
    char dp_path[4096], cert_path[4096];
    if (state && field) {
        uint64_t fp, fr;
        const char *comma = strchr(field, ',');
        char left[32] = {0};
        if (npos || !comma || comma - field >= 31) { usage(stderr); return 2; }
        memcpy(left, field, (size_t)(comma - field));
        if (!parse_u64(left, &fp) || !parse_u64(comma + 1, &fr)) { usage(stderr); return 2; }
        if (!find_files(state, fp, fr, dp_path, cert_path)) {
            fprintf(stderr, "no full-matching certificate with a matching DP file for %" PRIu64 "^%" PRIu64 " under %s\n", fp, fr, state);
            return 1;
        }
        fprintf(stderr, "using %s\n   and %s\n", cert_path, dp_path);
        positional[0] = dp_path;
        positional[1] = cert_path;
    } else if (npos != 2 || state || field) {
        usage(stderr);
        return 2;
    }

    dp_t dp;
    cert_t cert;
    if (!load_dp(positional[0], &dp)) return 1;
    if (!load_cert(positional[1], &cert)) return 1;
    cert_geometry(&cert, &dp);
    facts_t x = {0};
    x.verify_checksum = o.verify_checksum;
    x.dp_matches = memcmp(dp.digest, cert.dp_digest, 32) == 0;
    x.pair_ok = x.dp_matches && cert.p == dp.p && cert.r == dp.r && cert.count == dp.requests &&
                cert.structure_ok && cert.padding_ok && (cert.status != 0 || cert.matched == cert.count);
    check_primitive(&dp, cert.coef, &x.prim);
    if (o.verify_checksum) {
        fprintf(stderr, "hashing the certificate (%" PRIu64 " bytes)...\n", cert.file_size);
        x.checksum_done = cert_checksum(&cert, &x.checksum_matches);
    }

    bool ok = x.pair_ok && x.prim.primitive && (!o.verify_checksum || (x.checksum_done && x.checksum_matches));
    bool stdout_is_array = o.output && strcmp(o.output, "-") == 0;
    FILE *text = stdout_is_array ? stderr : stdout;

    render_t rep = {0};
    FILE *requests = NULL;
    if (o.output || o.requests) {
        if (cert.status != 0)
            snprintf(rep.error, sizeof rep.error, "the certificate's outcome is %" PRIu64 ", not a full matching: no array to write", cert.status);
        else if (!ok)
            snprintf(rep.error, sizeof rep.error, "the files are inconsistent (see above): nothing written");
        if (rep.error[0]) { rep.attempted = true; ok = false; }
        else {
            if (o.requests) {
                requests = fopen(o.requests, "wx");
                if (!requests) { fprintf(stderr, "cannot create %s: %s\n", o.requests, strerror(errno)); return 1; }
                fputs("request\tclass\tstripe\tsuffix\tchoice\tlabel\telement\tcoefficients_low_first\tcell\tposition\n", requests);
            }
            if (!render(&dp, &cert, &o, &rep, requests)) ok = false;
            if (requests) fclose(requests);
        }
    }

    report(text, &dp, &cert, &x);
    if (o.explain) explain(text);
    report_render(text, &dp, &cert, &rep, &o);
    if (o.output && rep.ok && !stdout_is_array) {
        char meta[4200];
        snprintf(meta, sizeof meta, "%s", o.meta ? o.meta : "");
        if (!o.meta) snprintf(meta, sizeof meta, "%s.meta.txt", o.output);
        FILE *m = fopen(meta, "wx");
        if (m) {
            report(m, &dp, &cert, &x);
            explain(m);
            report_render(m, &dp, &cert, &rep, &o);
            fclose(m);
            fprintf(text, "\nreport saved to %s\n", meta);
        } else {
            fprintf(stderr, "could not save the report to %s: %s\n", meta, strerror(errno));
        }
    }
    if (rep.ok && rep.full) {
        ok = ok && rep.rows == dp.rows && rep.occupied == dp.requests;
        for (uint64_t i = 0; i < dp.run_count; ++i)
            if (rep.run_min[i] != UINT64_MAX)
                ok = ok && rep.run_min[i] == rep.run_max[i] && (u128)dp.f * dp.f * dp.runs[i].omega == rep.run_min[i];
    }
    fprintf(text, "\n%s\n", ok ? "RESULT: consistent" : "RESULT: PROBLEMS FOUND (see above)");
    free(rep.run_min); free(rep.run_max); free(dp.runs);
    fclose(cert.file);
    return ok ? 0 : 1;
}
