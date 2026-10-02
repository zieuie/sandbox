#define _GNU_SOURCE

// Single-threaded CPU port of gpu_match.py's algorithm (offset greedy + APFB phases).
// It exists only to separate algorithmic gains from GPU hardware gains.

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define FREE UINT32_MAX

typedef struct {
    uint32_t *cells;
    uint64_t *bfirst;
    uint32_t *bcoset;
    uint32_t *bwidth;
    uint32_t nblocks;
    uint32_t f;
    uint32_t qm1;
} graph_t;

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static void decode(const graph_t *g, uint32_t u, uint32_t *coset, uint32_t *cell) {
    uint32_t lo = 0, hi = g->nblocks;
    while (lo + 1 < hi) {
        uint32_t m = (lo + hi) >> 1;
        if (g->bfirst[m] <= u) lo = m; else hi = m;
    }
    uint64_t off = u - g->bfirst[lo];
    *coset = g->bcoset[lo] + (uint32_t)(off / g->bwidth[lo]);
    *cell = (uint32_t)(off % g->bwidth[lo]);
}

static inline uint32_t shift(uint32_t label, uint32_t coset, uint32_t qm1) {
    if (label == 0) return 0;
    uint32_t x = label - 1;
    x = x >= coset ? x - coset : x + (qm1 - coset);
    return x + 1;
}

static inline uint32_t mix(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16;
    return x;
}

static void *xmalloc(size_t bytes) {
    void *p = malloc(bytes);
    if (p == NULL) { fprintf(stderr, "out of memory\n"); exit(1); }
    return p;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        puts("Usage: kh_cpu_reference FIELD.khgf BLOCKS.txt [SALT]\n"
             "Example: ./kh_cpu_reference /tmp/5_3.khgf /tmp/blocks.txt");
        return argc == 1 ? 0 : 1;
    }
    uint32_t salt = argc > 3 ? (uint32_t)strtoul(argv[3], NULL, 0) : 0x9E3779B9u;
    FILE *file = fopen(argv[1], "rb");
    char magic[4];
    uint32_t h[6];
    if (!file || fread(magic, 1, 4, file) != 4 || memcmp(magic, "KHGF", 4) || fread(h, sizeof h, 1, file) != 1) {
        fprintf(stderr, "bad field dump\n");
        return 1;
    }
    uint64_t q = h[2] | ((uint64_t)h[3] << 32);
    graph_t g = {0};
    g.f = h[4];
    g.qm1 = (uint32_t)(q - 1);
    g.cells = xmalloc(q * 4);
    if (fread(g.cells, 4, q, file) != q) { fprintf(stderr, "truncated field\n"); return 1; }
    fclose(file);

    file = fopen(argv[2], "r");
    uint64_t count, stripes = 0, cosets = 0;
    if (!file || fscanf(file, "%" SCNu64, &count) != 1) { fprintf(stderr, "bad blocks\n"); return 1; }
    g.nblocks = (uint32_t)count;
    g.bfirst = xmalloc(count * 8);
    g.bcoset = xmalloc(count * 4);
    g.bwidth = xmalloc(count * 4);
    for (uint64_t i = 0; i < count; ++i) {
        uint64_t a, copies;
        if (fscanf(file, "%" SCNu64 " %" SCNu64, &a, &copies) != 2) { fprintf(stderr, "bad blocks\n"); return 1; }
        g.bfirst[i] = stripes * g.f;
        g.bcoset[i] = (uint32_t)cosets;
        g.bwidth[i] = (uint32_t)(a * g.f);
        stripes += a * copies;
        cosets += copies;
    }
    fclose(file);
    uint32_t n = (uint32_t)(stripes * g.f);

    uint32_t *left = xmalloc((size_t)n * 4), *right = xmalloc(q * 4);
    uint16_t *choice = xmalloc((size_t)n * 2), *viak = xmalloc((size_t)n * 2);
    uint32_t *root = xmalloc((size_t)n * 4), *parent = xmalloc((size_t)n * 4);
    uint32_t *queue = xmalloc((size_t)n * 4), *roots = xmalloc((size_t)n * 4);
    uint8_t *done = xmalloc(n);
    uint32_t *end_u = xmalloc((size_t)n * 4), *end_v = xmalloc((size_t)n * 4);
    uint16_t *end_k = xmalloc((size_t)n * 2);
    memset(left, 255, (size_t)n * 4);
    memset(right, 255, q * 4);
    uint64_t scans = 0;
    uint32_t matched = 0;
    double start = now();

    // Greedy: first free neighbor from a hashed start offset.
    for (uint32_t u = 0; u < n; ++u) {
        uint32_t coset, cell;
        decode(&g, u, &coset, &cell);
        const uint32_t *row = g.cells + (uint64_t)cell * g.f;
        uint32_t s = salt ? mix(u ^ salt) % g.f : 0;
        for (uint32_t k = 0; k < g.f; ++k) {
            uint32_t kk = k + s >= g.f ? k + s - g.f : k + s;
            uint32_t v = shift(row[kk], coset, g.qm1);
            ++scans;
            if (right[v] == FREE) {
                right[v] = u; left[u] = v; choice[u] = (uint16_t)kk; ++matched;
                break;
            }
        }
    }
    fprintf(stderr, "greedy matched=%u/%u scans=%" PRIu64 " seconds=%.3f\n", matched, n, scans, now() - start);

    // APFB phases with a FIFO queue: one multi-source BFS, trees pruned once their root claims a free right.
    for (uint32_t phase = 1; matched < n; ++phase) {
        double t = now();
        uint32_t nroots = 0;
        for (uint32_t u = 0; u < n; ++u) {
            root[u] = FREE;
            if (left[u] == FREE) { root[u] = u; done[u] = 0; roots[nroots++] = u; queue[nroots - 1] = u; }
        }
        uint64_t head = 0, tail = nroots;
        uint32_t found = 0;
        while (head < tail) {
            uint32_t u = queue[head++], r = root[u];
            if (done[r]) continue;
            uint32_t coset, cell;
            decode(&g, u, &coset, &cell);
            const uint32_t *row = g.cells + (uint64_t)cell * g.f;
            for (uint32_t k = 0; k < g.f; ++k) {
                uint32_t v = shift(row[k], coset, g.qm1), w = right[v];
                ++scans;
                if (w == FREE) {
                    done[r] = 1; right[v] = FREE - 1; end_u[r] = u; end_v[r] = v; end_k[r] = (uint16_t)k; ++found;
                    break;
                }
                if (w != FREE - 1 && root[w] == FREE) {
                    root[w] = r; parent[w] = u; viak[w] = (uint16_t)k; queue[tail++] = w;
                }
            }
        }
        for (uint32_t i = 0; i < nroots; ++i) {
            uint32_t r = roots[i];
            if (!done[r]) continue;
            uint32_t cur = end_u[r], v = end_v[r];
            uint16_t k = end_k[r];
            for (;;) {
                uint32_t old = left[cur];
                left[cur] = v; choice[cur] = k; right[v] = cur;
                if (cur == r) break;
                k = viak[cur]; v = old; cur = parent[cur];
            }
            ++matched;
        }
        fprintf(stderr, "phase=%u augmented=%u matched=%u/%u visited=%" PRIu64 " seconds=%.3f\n",
                phase, found, matched, n, tail, now() - t);
        if (found == 0) break;
    }
    double seconds = now() - start;

    // Validate every selected edge and right ownership.
    for (uint32_t u = 0; u < n; ++u) {
        if (left[u] == FREE) continue;
        uint32_t coset, cell;
        decode(&g, u, &coset, &cell);
        if (shift(g.cells[(uint64_t)cell * g.f + choice[u]], coset, g.qm1) != left[u] || right[left[u]] != u) {
            fprintf(stderr, "invalid matching at %u\n", u);
            return 1;
        }
    }
    printf("{\"matched\":%u,\"required\":%u,\"scans\":%" PRIu64 ",\"solve_seconds\":%.3f}\n", matched, n, scans, seconds);
    return matched == n ? 0 : 2;
}
