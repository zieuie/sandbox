#define _GNU_SOURCE

// Exact GPU matching kernel with the same input/payload contract as kh_match_kernel.

#include "kh_cuda.h"
#include "kh_field.h"
#include "kh_resource.h"

#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define FREE UINT32_MAX

extern const kh_cuda_image_t kh_block_images[];
extern const unsigned kh_block_images_count;

static void help(void) {
    puts("Exact GPU matching over one primitive-X field that is larger than one GPU: the field is cut\n"
         "into blocks that are matched one after another (CUDA driver API). For fields that fit one GPU\n"
         "use kh_gpu_block_kernel instead.\n"
         "Usage: kh_gpu_block_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr]\n"
         "       [--start N] [--threads N] [--max-bytes N] [--device N] [--salt N]\n"
         "       [--block-device-bytes N] [--block-requests N] [--max-rounds N] [--max-residual N]\n"
         "Example: printf '1\\n1 1\\n' > /tmp/blocks.txt\n"
         "         ./kh_gpu_block_kernel 3 3 /tmp/blocks.txt /tmp/choices.bin\n"
         "Blocks, payload and metadata JSON match kh_gpu_block_kernel. Exit 0: full matching; 4: ended\n"
         "incomplete (never an obstruction); 1: error. --block-device-bytes sets the per-block device\n"
         "budget (default: free device memory minus 256 MiB); --block-requests caps requests per block\n"
         "(tests). --threads only affects CPU field construction.\n"
         "--test-cells FILE (tests only) replaces the field with q raw u32 labels, ascending per cell.");
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

// Same parser semantics as kh_match_kernel's polynomial_parse().
static bool polynomial_parse(const char *text, const kh_parameters_t *parameters, uint16_t *output) {
    const char *cursor = text;
    for (uint32_t index = 0; index <= parameters->r; ++index) {
        char *end = NULL;
        unsigned long long value = strtoull(cursor, &end, 10);
        if (end == cursor || value >= parameters->p ||
            (index < parameters->r ? *end != ',' : *end != '\0')) {
            return false;
        }
        output[index] = (uint16_t)value;
        cursor = end + 1;
    }
    return true;
}

// Same validation as kh_match_kernel's blocks_read(), producing device-friendly arrays.
static bool blocks_read(const char *path, const kh_parameters_t *parameters, uint64_t **first,
                        uint32_t **coset, uint32_t **width, uint32_t **copies_out, uint32_t *count, uint32_t *n) {
    FILE *file = fopen(path, "r");
    uint64_t blocks = 0;
    if (file == NULL) {
        return false;
    }
    if (fscanf(file, "%" SCNu64, &blocks) != 1 || blocks == 0 || blocks > parameters->budget) {
        fclose(file);
        return false;
    }
    *first = calloc(blocks, sizeof **first);
    *coset = calloc(blocks, sizeof **coset);
    *width = calloc(blocks, sizeof **width);
    *copies_out = calloc(blocks, sizeof **copies_out);
    uint64_t stripes = 0, cosets = 0;
    bool valid = *first != NULL && *coset != NULL && *width != NULL && *copies_out != NULL;
    for (uint64_t index = 0; valid && index < blocks; ++index) {
        uint64_t a, copies;
        if (fscanf(file, "%" SCNu64 " %" SCNu64, &a, &copies) != 2 || a == 0 || a > parameters->p ||
            copies == 0 || copies > parameters->budget / a || stripes + a * copies > parameters->budget) {
            valid = false;
            break;
        }
        (*first)[index] = stripes * parameters->f;
        (*coset)[index] = (uint32_t)cosets;
        (*width)[index] = (uint32_t)(a * parameters->f);
        (*copies_out)[index] = (uint32_t)copies;
        stripes += a * copies;
        cosets += copies;
    }
    char trailing;
    if (valid && (cosets + 1 > parameters->q - 1 || fscanf(file, " %c", &trailing) == 1 || ferror(file) ||
                  stripes * parameters->f > UINT32_MAX - 1)) {
        valid = false;
    }
    fclose(file);
    *count = (uint32_t)blocks;
    *n = (uint32_t)(stripes * parameters->f);
    return valid;
}

// Stream packed little-bit-first values exactly like kh_match_kernel's packed_write().
typedef struct {
    FILE *file;
    uint64_t accumulator;
    uint32_t available;
    bool ok;
} packer_t;

static void pack(packer_t *packer, uint32_t value, uint32_t bits) {
    packer->accumulator |= (uint64_t)value << packer->available;
    packer->available += bits;
    while (packer->available >= 8) {
        if (fputc((int)(packer->accumulator & 255), packer->file) == EOF) {
            packer->ok = false;
        }
        packer->accumulator >>= 8;
        packer->available -= 8;
    }
}

static void pack_flush(packer_t *packer) {
    if (packer->available != 0 && fputc((int)packer->accumulator, packer->file) == EOF) {
        packer->ok = false;
    }
    packer->accumulator = 0;
    packer->available = 0;
}

typedef struct {
    kh_cuda_t cuda;
    void *collect, *augment;
    kh_dptr_t allocations[64];
    unsigned allocated;
    const char *error;
} gpu_t;

static kh_dptr_t gpu_alloc(gpu_t *gpu, size_t bytes) {
    kh_dptr_t pointer = 0;
    if (gpu->error != NULL) {
        return 0;
    }
    if (gpu->allocated == 64 || gpu->cuda.cuMemAlloc(&pointer, bytes == 0 ? 4 : bytes) != 0) {
        gpu->error = "GPU allocation failed";
        return 0;
    }
    gpu->allocations[gpu->allocated++] = pointer;
    return pointer;
}

static void gpu_free_last(gpu_t *gpu, unsigned keep) {
    while (gpu->allocated > keep) {
        gpu->cuda.cuMemFree(gpu->allocations[--gpu->allocated]);
    }
}

static bool gpu_ok(gpu_t *gpu, int status, const char *what) {
    if (status != 0 && gpu->error == NULL) {
        static char message[256];
        snprintf(message, sizeof message, "%s: %s", what, kh_cuda_error(&gpu->cuda, status));
        gpu->error = message;
    }
    return gpu->error == NULL;
}

static uint32_t read_u32(gpu_t *gpu, kh_dptr_t pointer) {
    uint32_t value = 0;
    gpu_ok(gpu, gpu->cuda.cuMemcpyDtoH(&value, pointer, 4), "device read");
    return value;
}

static void progress(uint32_t done, uint32_t total, const char *phase) {
    printf("{\"done\":%u,\"total\":%u,\"checkpoint_done\":0,\"phase\":\"%s\",\"units\":\"requests\"}\n",
           done, total, phase);
    fflush(stdout);
}


// ---------------------------------------------------------------------------------------------
// Block mode: see docs/GPU_BLOCK_MATCHING.md. The field is cut into blocks of cells; block b owns
// the requests in its cell range and a contiguous window of right vertices. Each block is
// matched on its own, then requests left over are imported into blocks with free rights.
// ---------------------------------------------------------------------------------------------
#define EXTRA_MAX 256
#define NOCHOICE 0xFFFFu
#define MAX_ROUNDS_LIMIT 4096

// Mirrors bgraph_t in kernels.cu.
typedef struct {
    kh_dptr_t rows, lfirst, lcoset, lwidth, icoset, islot;
    uint32_t nblocks, f, qm1, nown, n, lo, hi, pad;
} device_bgraph_t;

typedef struct {
    uint32_t home;     // block that owns the request
    uint32_t li;       // its local index there
    uint32_t cursor;   // next candidate offset when it is looking for a block
} member_t;

typedef struct {
    uint32_t c_lo, c_hi, nseg;
    uint64_t m, rlo, rhi, matched;
    uint32_t *segj, *lfirst, *lcoset, *lwidth;
    uint16_t *lchoice;
    uint32_t extra[EXTRA_MAX];
    uint32_t nextra;
    member_t *imp;
    size_t nimp;
    uint64_t imp_limit;
} block_t;

typedef struct {
    gpu_t *gpu;
    void *greedy_w, *expand_w, *restore_w, *count_free, *check_w;
    uint64_t q, f, n;
    uint32_t nblocks_dp, *bcoset, *bwidth, *copies;
    uint64_t *bfirst;
    const uint32_t *cells;
    uint32_t salt;
    block_t *blocks;
    uint32_t nblock;
    uint64_t phases, scans;
} bctx_t;

static void member_request(const block_t *blk, uint32_t li, uint32_t *coset, uint32_t *cell) {
    uint32_t lo = 0, hi = blk->nseg;
    while (lo + 1 < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (blk->lfirst[mid] <= li) lo = mid; else hi = mid;
    }
    uint32_t off = li - blk->lfirst[lo];
    *coset = blk->lcoset[lo] + off / blk->lwidth[lo];
    *cell = blk->c_lo + off % blk->lwidth[lo];
}

static bool slot_of(const block_t *blk, uint32_t cell, uint32_t *slot) {
    if (cell >= blk->c_lo && cell < blk->c_hi) {
        *slot = cell - blk->c_lo;
        return true;
    }
    for (uint32_t index = 0; index < blk->nextra; ++index) {
        if (blk->extra[index] == cell) {
            *slot = blk->c_hi - blk->c_lo + index;
            return true;
        }
    }
    return false;
}

// One matching of one block on the device, with the imports it currently holds plus `fresh`.
static bool run_block(bctx_t *x, uint32_t b, const member_t *fresh, size_t nfresh, bool first) {
    gpu_t *gpu = x->gpu;
    block_t *blk = &x->blocks[b];
    uint32_t f = (uint32_t)x->f;
    size_t nimp = blk->nimp + nfresh;
    uint32_t nown = (uint32_t)blk->m;
    uint32_t n = nown + (uint32_t)nimp;
    uint32_t cells = blk->c_hi - blk->c_lo;
    uint64_t window = blk->rhi - blk->rlo;
    unsigned mark = gpu->allocated;
    uint32_t *icoset = malloc(nimp * 4 + 4), *islot = malloc(nimp * 4 + 4);
    uint16_t *hchoice = malloc((size_t)n * 2 + 4);
    if (icoset == NULL || islot == NULL || hchoice == NULL) {
        gpu->error = "host allocation failed";
        free(icoset); free(islot); free(hchoice);
        return false;
    }
    memcpy(hchoice, blk->lchoice, (size_t)nown * 2);
    for (size_t index = 0; index < nimp; ++index) {
        member_t m = index < blk->nimp ? blk->imp[index] : fresh[index - blk->nimp];
        const block_t *home = &x->blocks[m.home];
        uint32_t cell;
        member_request(home, m.li, &icoset[index], &cell);
        if (!slot_of(blk, cell, &islot[index])) {
            gpu->error = "imported request has no uploaded row";
            break;
        }
        hchoice[nown + index] = home->lchoice[m.li];
    }
    uint32_t slots = cells + blk->nextra;
    device_bgraph_t g = {0};
    g.nblocks = blk->nseg; g.f = f; g.qm1 = (uint32_t)(x->q - 1); g.nown = nown; g.n = n;
    g.lo = (uint32_t)blk->rlo; g.hi = (uint32_t)blk->rhi;
    g.rows = gpu_alloc(gpu, (uint64_t)slots * f * 4);
    g.lfirst = gpu_alloc(gpu, blk->nseg * 4);
    g.lcoset = gpu_alloc(gpu, blk->nseg * 4);
    g.lwidth = gpu_alloc(gpu, blk->nseg * 4);
    g.icoset = gpu_alloc(gpu, nimp * 4);
    g.islot = gpu_alloc(gpu, nimp * 4);
    kh_dptr_t left = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t choice = gpu_alloc(gpu, (uint64_t)n * 2 + 4);
    kh_dptr_t right = gpu_alloc(gpu, window * 4);
    kh_dptr_t root = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t parent = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t viak = gpu_alloc(gpu, (uint64_t)n * 2 + 4);
    kh_dptr_t queue_a = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t queue_b = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t counters = gpu_alloc(gpu, 64);
    kh_dptr_t scans = counters + 32;
    if (gpu->error == NULL) {
        gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.rows, x->cells + (uint64_t)blk->c_lo * f, (uint64_t)cells * f * 4), "upload rows");
        for (uint32_t index = 0; gpu->error == NULL && index < blk->nextra; ++index) {
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.rows + ((uint64_t)cells + index) * f * 4,
                                               x->cells + (uint64_t)blk->extra[index] * f, (uint64_t)f * 4), "upload rows");
        }
        gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.lfirst, blk->lfirst, blk->nseg * 4), "upload segments");
        gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.lcoset, blk->lcoset, blk->nseg * 4), "upload segments");
        gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.lwidth, blk->lwidth, blk->nseg * 4), "upload segments");
        if (nimp != 0) {
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.icoset, icoset, nimp * 4), "upload imports");
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.islot, islot, nimp * 4), "upload imports");
        }
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(right, FREE, window), "init");
        gpu_ok(gpu, gpu->cuda.cuMemsetD8(counters, 0, 64), "init");
        if (first) {
            gpu_ok(gpu, gpu->cuda.cuMemsetD32(left, FREE, n), "init");
            gpu_ok(gpu, gpu->cuda.cuMemsetD8(choice, 0xFF, (uint64_t)n * 2), "init");
        } else {
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(choice, hchoice, (uint64_t)n * 2), "upload choices");
            kh_dptr_t bad = counters + 16;
            void *restore_args[] = {&g, &choice, &left, &right, &bad};
            gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, x->restore_w, (n + 255) / 256, 256, restore_args), "restore");
            if (gpu->error == NULL && read_u32(gpu, bad) != 0) {
                gpu->error = "restored block state is inconsistent";
            }
        }
    }
    kh_dptr_t matched_counter = counters;
    uint32_t salt = x->salt;
    void *greedy_args[] = {&g, &left, &choice, &right, &matched_counter, &scans, &salt};
    unsigned warp_blocks = (unsigned)(((uint64_t)n * 32 + 255) / 256);
    if (gpu->error == NULL) {
        gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, x->greedy_w, warp_blocks, 256, greedy_args), "greedy");
        gpu_ok(gpu, gpu->cuda.cuCtxSynchronize(), "greedy");
    }
    unsigned base = gpu->allocated;
    while (gpu->error == NULL) {
        kh_dptr_t root_count = counters + 20;
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(root_count, 0, 1), "phase init");
        void *count_args[] = {&n, &left, &root_count};
        gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, x->count_free, (n + 255) / 256, 256, count_args), "count");
        uint32_t nroots = gpu->error == NULL ? read_u32(gpu, root_count) : 0;
        if (gpu->error != NULL || nroots == 0) {
            break;
        }
        gpu_free_last(gpu, base);
        kh_dptr_t roots = gpu_alloc(gpu, (uint64_t)nroots * 4);
        kh_dptr_t rootdone = gpu_alloc(gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_u = gpu_alloc(gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_v = gpu_alloc(gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_k = gpu_alloc(gpu, (uint64_t)nroots * 2 + 4);
        if (gpu->error != NULL) {
            break;
        }
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(rootdone, 0, nroots), "phase init");
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(counters + 4, 0, 5), "phase init");
        void *collect_args[] = {&n, &left, &root, &roots, &root_count};
        gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, gpu->collect, (n + 255) / 256, 256, collect_args), "collect");
        if (gpu->error == NULL && read_u32(gpu, root_count) != nroots) {
            gpu->error = "free request count changed during collection";
            break;
        }
        kh_dptr_t front = roots, next = queue_a;
        uint32_t count = nroots;
        kh_dptr_t tail = counters + 8, found = counters + 4, longest = counters + 12;
        while (gpu->error == NULL && count != 0) {
            gpu_ok(gpu, gpu->cuda.cuMemsetD32(tail, 0, 1), "level");
            void *expand_args[] = {&g, &count, &front, &next, &tail, &right, &root, &parent, &viak,
                                   &rootdone, &end_u, &end_v, &end_k, &found, &scans};
            unsigned blocks = (unsigned)(((uint64_t)count * 32 + 255) / 256);
            gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, x->expand_w, blocks, 256, expand_args), "expand");
            count = read_u32(gpu, tail);
            front = next;
            next = next == queue_a ? queue_b : queue_a;
        }
        uint32_t augmented = gpu->error == NULL ? read_u32(gpu, found) : 0;
        if (augmented != 0) {
            void *augment_args[] = {&nroots, &roots, &left, &choice, &right, &parent, &viak, &rootdone,
                                    &end_u, &end_v, &end_k, &counters, &longest};
            gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, gpu->augment, (nroots + 255) / 256, 256, augment_args), "augment");
        }
        ++x->phases;
        if (augmented == 0) {
            break;
        }
    }
    uint32_t checked = 0;
    if (gpu->error == NULL) {
        kh_dptr_t bad = counters + 16, matched_total = counters + 24;
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(bad, 0, 1), "check");
        gpu_ok(gpu, gpu->cuda.cuMemsetD32(matched_total, 0, 1), "check");
        void *check_args[] = {&g, &left, &choice, &right, &bad, &matched_total};
        gpu_ok(gpu, kh_cuda_launch(&gpu->cuda, x->check_w, (n + 255) / 256, 256, check_args), "check");
        if (gpu->error == NULL && read_u32(gpu, bad) != 0) {
            gpu->error = "device self-check failed";
        }
        checked = read_u32(gpu, matched_total);
        uint64_t scan_count = 0;
        gpu_ok(gpu, gpu->cuda.cuMemcpyDtoH(&scan_count, scans, 8), "scans");
        x->scans += scan_count;
        gpu_ok(gpu, gpu->cuda.cuMemcpyDtoH(hchoice, choice, (uint64_t)n * 2), "download");
    }
    gpu_free_last(gpu, mark);
    free(icoset);
    free(islot);
    if (gpu->error != NULL) {
        free(hchoice);
        return false;
    }
    memcpy(blk->lchoice, hchoice, (size_t)nown * 2);
    member_t *kept = malloc((nimp + 1) * sizeof *kept);
    if (kept == NULL) {
        free(hchoice);
        gpu->error = "host allocation failed";
        return false;
    }
    size_t count = 0;
    for (size_t index = 0; index < nimp; ++index) {
        member_t m = index < blk->nimp ? blk->imp[index] : fresh[index - blk->nimp];
        x->blocks[m.home].lchoice[m.li] = hchoice[nown + index];
        if (hchoice[nown + index] != NOCHOICE) {
            kept[count++] = m;
        }
    }
    free(blk->imp);
    blk->imp = kept;
    blk->nimp = count;
    blk->matched = checked;
    free(hchoice);
    return true;
}

static uint32_t host_shift(uint32_t label, uint32_t coset, uint32_t qm1) {
    if (label == 0) return 0;
    uint32_t v = label - 1;
    v = v >= coset ? v - coset : v + (qm1 - coset);
    return v + 1;
}

static uint64_t block_cost(uint64_t f, uint64_t cells, uint64_t m) {
    uint64_t imports = m / 64 + 64;
    return 4 * f * (cells + EXTRA_MAX) + 31 * (m + imports) + 4 * m + UINT64_C(16777216);
}

// Cut the used cells into `count` blocks of nearly equal request counts (a tiny remainder block
// would have too few neighbors per request to match on its own). Returns false if any block
// is over budget. With `out` NULL it only checks.
static bool cut_blocks(bctx_t *x, uint32_t count, uint64_t budget, const uint64_t *per_cell, uint64_t ncells,
                       uint32_t *bounds) {
    uint64_t f = x->f, total = 0;
    for (uint64_t c = 0; c < ncells; ++c) total += per_cell[c];
    uint64_t c = 0, cumulative = 0;
    for (uint32_t k = 0; k < count; ++k) {
        uint64_t target = (uint64_t)((__uint128_t)total * (k + 1) / count);
        uint64_t start = c, m = 0;
        // Leave at least one cell for every block still to come.
        while (c < ncells && (k + 1 == count || c == start || cumulative + per_cell[c] / 2 < target) &&
               (k + 1 == count || ncells - c > count - k - 1)) {
            cumulative += per_cell[c];
            m += per_cell[c];
            ++c;
        }
        if (c == start || block_cost(f, c - start, m) > budget) return false;
        bounds[k] = (uint32_t)c;
    }
    return c == ncells;
}

// Cut the used cells into the fewest equal blocks that fit the device budget (and the optional
// per-block request cap used by tests). Returns false with an error text if none does.
static bool layout_blocks(bctx_t *x, uint64_t budget, uint64_t max_requests, const char **error) {
    uint64_t f = x->f;
    uint64_t amax = 0;
    for (uint32_t j = 0; j < x->nblocks_dp; ++j) {
        if (x->bwidth[j] / f > amax) amax = x->bwidth[j] / f;
    }
    uint64_t ncells = amax * f;
    uint64_t *per_cell = calloc(ncells, sizeof *per_cell);
    uint32_t *bounds = NULL;
    x->blocks = NULL;
    if (per_cell == NULL) {
        *error = "host allocation failed";
        return false;
    }
    for (uint64_t c = 0; c < ncells; ++c) {
        for (uint32_t j = 0; j < x->nblocks_dp; ++j) {
            if (c < x->bwidth[j]) per_cell[c] += x->copies[j];
        }
    }
    uint64_t count = max_requests ? (x->n + max_requests - 1) / max_requests : 1;
    if (count == 0) count = 1;
    if (count > ncells) count = ncells;
    for (;; ++count) {
        if (count > ncells) {
            free(per_cell);
            *error = "no block layout fits the device budget";
            return false;
        }
        bounds = realloc(bounds, count * 4);
        if (bounds == NULL) {
            *error = "host allocation failed";
            return false;
        }
        if (cut_blocks(x, (uint32_t)count, budget, per_cell, ncells, bounds)) break;
    }
    x->blocks = calloc(count, sizeof *x->blocks);
    if (x->blocks == NULL) {
        *error = "host allocation failed";
        return false;
    }
    uint64_t rlo = 0;
    for (uint32_t k = 0; k < count; ++k) {
        uint32_t start = k == 0 ? 0 : bounds[k - 1], end = bounds[k];
        block_t *blk = &x->blocks[k];
        for (uint32_t c = start; c < end; ++c) blk->m += per_cell[c];
        blk->c_lo = start;
        blk->c_hi = end;
        blk->rlo = rlo;
        rlo += blk->m;
        blk->rhi = rlo;
        blk->imp_limit = blk->m / 64 + 64;
        blk->segj = calloc(x->nblocks_dp, 4);
        blk->lfirst = calloc(x->nblocks_dp, 4);
        blk->lcoset = calloc(x->nblocks_dp, 4);
        blk->lwidth = calloc(x->nblocks_dp, 4);
        blk->lchoice = malloc(blk->m * 2 + 4);
        if (!blk->segj || !blk->lfirst || !blk->lcoset || !blk->lwidth || !blk->lchoice) {
            *error = "host allocation failed";
            return false;
        }
        memset(blk->lchoice, 0xFF, blk->m * 2);
        uint32_t local = 0;
        for (uint32_t j = 0; j < x->nblocks_dp; ++j) {
            if (start >= x->bwidth[j]) continue;
            uint64_t last = end < x->bwidth[j] ? end : x->bwidth[j];
            blk->segj[blk->nseg] = j;
            blk->lfirst[blk->nseg] = local;
            blk->lcoset[blk->nseg] = x->bcoset[j];
            blk->lwidth[blk->nseg] = (uint32_t)(last - start);
            local += x->copies[j] * (uint32_t)(last - start);
            ++blk->nseg;
        }
    }
    x->nblock = (uint32_t)count;
    x->blocks[count - 1].rhi = x->q;  // spare rights (n < q) go to the last window
    free(per_cell);
    free(bounds);
    return true;
}

typedef struct {
    uint64_t imports, matched;
    double seconds;
} round_t;



// Returns the process exit code: 0 full matching, 4 incomplete, 1 error.
static int solve_blocks(bctx_t *x, uint64_t budget, uint64_t max_requests, uint64_t max_rounds,
                        uint64_t max_residual, const uint16_t *polynomial_in, const char *output,
                        const kh_parameters_t *parameters, uint32_t candidate, double t0, double t_field,
                        const char *device_name) {
    (void)polynomial_in;
    gpu_t *gpu = x->gpu;
    const char *error = NULL;
    uint32_t f = (uint32_t)x->f;
    if (!layout_blocks(x, budget, max_requests, &error)) {
        fprintf(stderr, "kh_gpu_block_kernel: %s\n", error);
        return 1;
    }
    uint32_t pn = x->nblock;
    fprintf(stderr, "blocks=%u budget=%" PRIu64 "\n", pn, budget);
    double t_start = now();
    uint64_t *trace = malloc(((uint64_t)pn + MAX_ROUNDS_LIMIT + 2) * sizeof *trace);
    uint64_t trace_count = 0;
    if (trace == NULL) {
        fprintf(stderr, "kh_gpu_block_kernel: host allocation failed\n");
        return 1;
    }
    trace[trace_count++] = x->n;
    for (uint32_t b = 0; b < pn; ++b) {
        char phase[64];
        snprintf(phase, sizeof phase, "block %u/%u", b + 1, pn);
        if (!run_block(x, b, NULL, 0, true)) {
            fprintf(stderr, "kh_gpu_block_kernel: %s\n", gpu->error);
            return 1;
        }
        uint64_t done = 0;
        for (uint32_t i = 0; i <= b; ++i) done += x->blocks[i].matched;
        trace[trace_count++] = x->n - done;
        progress((uint32_t)done, (uint32_t)x->n, phase);
        fprintf(stderr, "block %u/%u cells=[%u,%u) requests=%" PRIu64 " matched=%" PRIu64 "\n",
                b + 1, pn, x->blocks[b].c_lo, x->blocks[b].c_hi, x->blocks[b].m, x->blocks[b].matched);
    }
    uint64_t total_matched = 0;
    for (uint32_t b = 0; b < pn; ++b) total_matched += x->blocks[b].matched;
    uint64_t residual = x->n - total_matched, residual1 = residual;
    member_t *pending = malloc((residual + 1) * sizeof *pending);
    size_t npending = 0;
    if (pending == NULL) {
        fprintf(stderr, "kh_gpu_block_kernel: host allocation failed\n");
        return 1;
    }
    for (uint32_t b = 0; b < pn; ++b) {
        for (uint64_t li = 0; li < x->blocks[b].m; ++li) {
            if (x->blocks[b].lchoice[li] == NOCHOICE) {
                if (npending == residual) {
                    fprintf(stderr, "kh_gpu_block_kernel: matched count disagrees with free requests\n");
                    return 1;
                }
                pending[npending++] = (member_t){b, (uint32_t)li, 0};
            }
        }
    }
    if (npending != residual) {
        fprintf(stderr, "kh_gpu_block_kernel: matched count disagrees with free requests\n");
        return 1;
    }
    fprintf(stderr, "round 1 residual=%" PRIu64 "\n", residual);
    round_t rounds[MAX_ROUNDS_LIMIT] = {{0}};
    uint64_t round_count = 1;
    rounds[0] = (round_t){0, total_matched, now() - t_start};
    bool stuck = residual > max_residual;
    member_t **fresh = calloc(pn, sizeof *fresh);
    size_t *nfresh = calloc(pn, sizeof *nfresh);
    uint64_t *cap = calloc(pn, sizeof *cap);
    if (fresh == NULL || nfresh == NULL || cap == NULL) {
        fprintf(stderr, "kh_gpu_block_kernel: host allocation failed\n");
        return 1;
    }
    for (uint32_t b = 0; b < pn; ++b) fresh[b] = malloc((npending + 1) * sizeof **fresh);
    while (!stuck && npending != 0 && round_count < max_rounds) {
        double tr = now();
        for (uint32_t b = 0; b < pn; ++b) {
            nfresh[b] = 0;
            cap[b] = (x->blocks[b].rhi - x->blocks[b].rlo) - x->blocks[b].matched;
        }
        uint64_t assigned = 0;
        for (size_t i = 0; i < npending; ++i) {
            member_t *u = &pending[i];
            uint32_t coset, cell;
            member_request(&x->blocks[u->home], u->li, &coset, &cell);
            for (uint32_t attempt = 0; pn > 1 && attempt < pn - 1; ++attempt) {
                uint32_t b = (u->home + 1 + (u->cursor++ % (pn - 1))) % pn;
                block_t *blk = &x->blocks[b];
                uint32_t slot;
                if (cap[b] == 0 || blk->nimp + nfresh[b] >= blk->imp_limit) continue;
                if (!slot_of(blk, cell, &slot)) {
                    if (blk->nextra == EXTRA_MAX) continue;
                    blk->extra[blk->nextra++] = cell;
                }
                --cap[b];
                fresh[b][nfresh[b]++] = *u;
                ++assigned;
                break;
            }
        }
        if (assigned == 0) break;
        for (uint32_t b = 0; b < pn; ++b) {
            if (nfresh[b] == 0) continue;
            if (!run_block(x, b, fresh[b], nfresh[b], false)) {
                fprintf(stderr, "kh_gpu_block_kernel: %s\n", gpu->error);
                return 1;
            }
        }
        size_t kept = 0;
        for (size_t i = 0; i < npending; ++i) {
            if (x->blocks[pending[i].home].lchoice[pending[i].li] == NOCHOICE) pending[kept++] = pending[i];
        }
        total_matched = 0;
        for (uint32_t b = 0; b < pn; ++b) total_matched += x->blocks[b].matched;
        if (x->n - total_matched != kept) {
            fprintf(stderr, "kh_gpu_block_kernel: matched count disagrees with free requests\n");
            return 1;
        }
        rounds[round_count++] = (round_t){assigned, total_matched, now() - tr};
        trace[trace_count++] = x->n - total_matched;
        fprintf(stderr, "round %" PRIu64 " imports=%" PRIu64 " residual=%zu seconds=%.3f\n",
                round_count, assigned, kept, now() - tr);
        npending = kept;
        progress((uint32_t)total_matched, (uint32_t)x->n, "exchange");
    }
    residual = npending;
    double t_solve = now();
    bool complete = residual == 0;
    uint64_t imported = 0;
    for (uint32_t b = 0; b < pn; ++b) imported += x->blocks[b].nimp;
    uint16_t polynomial[32];
    memcpy(polynomial, polynomial_in, sizeof polynomial);
    gpu_free_last(gpu, 0);
    char device[128];
    snprintf(device, sizeof device, "%s", device_name);
    kh_cuda_close(&gpu->cuda);

    // Canonical-order walk: uniqueness of rights (host check) and the packed payload.
    uint64_t q = x->q;
    FILE *file = NULL;
    packer_t packer = {NULL, 0, 0, true};
    uint8_t *used = NULL;
    bool success = true;
    if (complete) {
        used = calloc((q + 7) / 8, 1);
        int descriptor = open(output, O_WRONLY | O_CREAT | O_EXCL, 0600);
        file = descriptor < 0 ? NULL : fdopen(descriptor, "wb");
        packer.file = file;
        packer.ok = file != NULL && used != NULL;
        uint32_t bits = 0;
        for (uint32_t value = f - 1; value != 0; value >>= 1) ++bits;
        uint32_t *off = malloc(pn * 4), *clip = malloc(pn * 4);
        uint32_t amax_cells = x->blocks[pn - 1].c_hi;
        uint32_t *cell_block = malloc((size_t)amax_cells * 4);
        if (off == NULL || clip == NULL || cell_block == NULL) packer.ok = false;
        for (uint32_t b = 0; packer.ok && b < pn; ++b) {
            for (uint32_t c = x->blocks[b].c_lo; c < x->blocks[b].c_hi; ++c) cell_block[c] = b;
        }
        for (uint32_t j = 0; packer.ok && j < x->nblocks_dp; ++j) {
            for (uint32_t b = 0; b < pn; ++b) {
                off[b] = clip[b] = 0;
                for (uint32_t s = 0; s < x->blocks[b].nseg; ++s) {
                    if (x->blocks[b].segj[s] == j) {
                        off[b] = x->blocks[b].lfirst[s];
                        clip[b] = x->blocks[b].lwidth[s];
                    }
                }
            }
            for (uint32_t coset = 0; packer.ok && coset < x->copies[j]; ++coset) {
                for (uint32_t cell = 0; packer.ok && cell < x->bwidth[j]; ++cell) {
                    const block_t *blk = &x->blocks[cell_block[cell]];
                    uint32_t b = cell_block[cell];
                    uint32_t li = off[b] + coset * clip[b] + (cell - blk->c_lo);
                    uint32_t k = blk->lchoice[li];
                    uint32_t v = k < f ? host_shift(x->cells[(uint64_t)cell * f + k], x->bcoset[j] + coset, (uint32_t)(q - 1)) : UINT32_MAX;
                    if (k >= f || used[v >> 3] >> (v & 7) & 1) {
                        fprintf(stderr, "kh_gpu_block_kernel: final matching is inconsistent\n");
                        packer.ok = false;
                        break;
                    }
                    used[v >> 3] |= (uint8_t)(1u << (v & 7));
                    pack(&packer, k, bits);
                }
            }
        }
        free(off); free(clip); free(cell_block);
        pack_flush(&packer);
        success = packer.ok && file != NULL && fflush(file) == 0 && fsync(fileno(file)) == 0;
        if (file == NULL || fclose(file) != 0) success = false;
        if (!success) {
            unlink(output);
            fprintf(stderr, "kh_gpu_block_kernel: payload write failed\n");
            return 1;
        }
    }
    double t_end = now();
    printf("{\"p\":%u,\"r\":%u,\"candidate\":%u,\"polynomial\":[", parameters->p, parameters->r, candidate);
    for (uint32_t index = 0; index <= parameters->r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    printf("],\"status\":%u,\"required\":%" PRIu64 ",\"matched\":%" PRIu64 ",\"phases\":%" PRIu64
           ",\"scans\":%" PRIu64 ",\"hall_left\":0,\"hall_right\":0,\"engine\":\"gpu-blocks\",\"device\":\"%s\","
           "\"blocks\":%u,\"rounds\":%" PRIu64 ",\"residual_round1\":%" PRIu64 ",\"residual\":%" PRIu64
           ",\"imported\":%" PRIu64 ",\"incomplete\":%s,\"round_log\":[",
           complete ? 0u : 2u, x->n, x->n - residual, x->phases, x->scans, device, pn, round_count, residual1,
           residual, imported, complete ? "false" : "true");
    for (uint64_t r = 0; r < round_count; ++r) {
        printf("%s{\"round\":%" PRIu64 ",\"imports\":%" PRIu64 ",\"matched\":%" PRIu64 ",\"seconds\":%.3f}",
               r == 0 ? "" : ",", r + 1, rounds[r].imports, rounds[r].matched, rounds[r].seconds);
    }
    printf("],\"trace\":[");
    for (uint64_t k = 0; k < trace_count; ++k) {
        printf("%s[%" PRIu64 ",%" PRIu64 "]", k == 0 ? "" : ",", k, trace[k]);
    }
    printf("],\"seconds\":{\"setup\":0.0,\"field\":%.3f,\"blocks\":%.3f,\"exchange\":%.3f,\"output\":%.3f}}\n",
           t_field - t0, rounds[0].seconds, t_solve - t_start - rounds[0].seconds, t_end - t_solve);
    fflush(stdout);
    free(used);
    return complete ? 0 : 4;
}

int main(int argc, char **argv) {
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }
    if (argc < 5) {
        help();
        return 1;
    }
    uint64_t p, r, threads = 1, start = 1, maximum = UINT64_C(2147483648), device = 0, salt = 0x9E3779B9u;
    uint64_t block_budget = 0, block_requests = 0, max_rounds = 16, max_residual = UINT64_MAX;
    const char *poly_text = NULL, *test_cells = NULL, *error = NULL;
    kh_parameters_t parameters;

    if (!kh_parse_u64(argv[1], &p) || !kh_parse_u64(argv[2], &r) || p > UINT32_MAX || r > UINT32_MAX ||
        !kh_parameters((uint32_t)p, (uint32_t)r, &parameters, &error)) {
        fprintf(stderr, "kh_gpu_block_kernel: invalid dimensions\n");
        return 1;
    }
    for (int index = 5; index < argc; ++index) {
        const char *option = argv[index];
        if (++index >= argc) {
            help();
            return 1;
        }
        if (!strcmp(option, "--poly")) {
            poly_text = argv[index];
            continue;
        }
        if (!strcmp(option, "--test-cells")) {
            test_cells = argv[index];
            continue;
        }
        uint64_t value;
        if (!kh_parse_u64(argv[index], &value)) {
            fprintf(stderr, "kh_gpu_block_kernel: invalid value for %s\n", option);
            return 1;
        }
        if (!strcmp(option, "--threads")) threads = value;
        else if (!strcmp(option, "--start")) start = value;
        else if (!strcmp(option, "--max-bytes")) maximum = value;
        else if (!strcmp(option, "--device")) device = value;
        else if (!strcmp(option, "--salt")) salt = value;
        else if (!strcmp(option, "--block-device-bytes")) block_budget = value;
        else if (!strcmp(option, "--block-requests")) block_requests = value;
        else if (!strcmp(option, "--max-rounds")) max_rounds = value;
        else if (!strcmp(option, "--max-residual")) max_residual = value;
        else {
            fprintf(stderr, "kh_gpu_block_kernel: unsupported option %s\n", option);
            return 1;
        }
    }
    if (threads == 0 || threads > 1024 || start > UINT32_MAX || maximum == 0 || device > 64 ||
        salt > UINT32_MAX || parameters.f > 65534 || max_rounds == 0 || max_rounds > MAX_ROUNDS_LIMIT) {
        fprintf(stderr, "kh_gpu_block_kernel: invalid resource controls, F above 65534, or --max-rounds outside 1..%d\n",
                MAX_ROUNDS_LIMIT);
        return 1;
    }
    uint64_t *bfirst = NULL;
    uint32_t *bcoset = NULL, *bwidth = NULL, *bcopies = NULL, nblocks = 0, n = 0;
    if (!blocks_read(argv[3], &parameters, &bfirst, &bcoset, &bwidth, &bcopies, &nblocks, &n)) {
        fprintf(stderr, "kh_gpu_block_kernel: invalid request blocks\n");
        return 1;
    }
    uint64_t q = parameters.q, f = parameters.f;
    if (max_residual == UINT64_MAX) {
        max_residual = n / 1000 + 16;
    }
    // Full field table (4q), choices (2n), and the field builder's per-thread positions.
    uint64_t host_required = 4 * q + 2 * (uint64_t)n + 4 * (uint64_t)parameters.budget * threads +
                             UINT64_C(8388608) * threads + UINT64_C(67108864);
    if (host_required > maximum) {
        fprintf(stderr, "kh_gpu_block_kernel: requires at least %" PRIu64 " host bytes; limit=%" PRIu64 "\n",
                host_required, maximum);
        return 1;
    }

    double t0 = now();
    gpu_t gpu = {0};
    if (!kh_cuda_open(&gpu.cuda, (int)device, kh_block_images, kh_block_images_count, &error)) {
        fprintf(stderr, "kh_gpu_block_kernel: %s\n", error);
        return 1;
    }
    size_t free_bytes = 0, total_bytes = 0;
    gpu.cuda.cuMemGetInfo(&free_bytes, &total_bytes);
    uint64_t budget = block_budget;
    if (budget == 0) {
        budget = free_bytes > UINT64_C(268435456) ? free_bytes - UINT64_C(268435456) : 0;
    }
    bctx_t bx = {0};
    bx.gpu = &gpu;
    gpu.collect = kh_cuda_function(&gpu.cuda, "collect_roots");
    gpu.augment = kh_cuda_function(&gpu.cuda, "augment");
    bx.greedy_w = kh_cuda_function(&gpu.cuda, "greedy_w");
    bx.expand_w = kh_cuda_function(&gpu.cuda, "expand_w");
    bx.restore_w = kh_cuda_function(&gpu.cuda, "restore_w");
    bx.count_free = kh_cuda_function(&gpu.cuda, "count_free");
    bx.check_w = kh_cuda_function(&gpu.cuda, "check_w");
    if (!gpu.collect || !gpu.augment || !bx.greedy_w || !bx.expand_w || !bx.restore_w || !bx.count_free || !bx.check_w) {
        fprintf(stderr, "kh_gpu_block_kernel: embedded module lacks kernels\n");
        kh_cuda_close(&gpu.cuda);
        return 1;
    }

    uint16_t polynomial[32] = {0};
    uint32_t candidate = 0;
    if (poly_text != NULL) {
        if (!polynomial_parse(poly_text, &parameters, polynomial) || !kh_primitive(&parameters, polynomial)) {
            fprintf(stderr, "kh_gpu_block_kernel: polynomial is not primitive with generator X\n");
            return 1;
        }
    } else if (!kh_generate_polynomial(&parameters, (uint32_t)start, polynomial, &candidate)) {
        fprintf(stderr, "kh_gpu_block_kernel: primitive polynomial candidates exhausted\n");
        return 1;
    }
    kh_field_t field = {0};
    if (test_cells != NULL) {
        // Test-only: q raw little-endian u32 labels replace the field (synthetic deficient graphs).
        FILE *cells = fopen(test_cells, "rb");
        field.cells = malloc(q * 4);
        bool loaded = cells != NULL && field.cells != NULL && fread(field.cells, 4, q, cells) == q;
        if (cells != NULL) {
            fclose(cells);
        }
        for (uint64_t index = 0; loaded && index < q; ++index) {
            loaded = field.cells[index] < q;
        }
        if (!loaded) {
            fprintf(stderr, "kh_gpu_block_kernel: invalid --test-cells table\n");
            return 1;
        }
    } else if (!kh_build_field(&parameters, polynomial, (uint32_t)threads, maximum, &field, &error)) {
        fprintf(stderr, "kh_gpu_block_kernel: %s\n", error);
        return 1;
    }
    double t_field = now();

    // Rows must ascend (real fields do by construction; --test-cells tables must be sorted too).
    for (uint64_t cell = 0; cell < parameters.budget && cell * f < q; ++cell) {
        for (uint64_t k = 1; k < f && cell * f + k < q; ++k) {
            if (field.cells[cell * f + k - 1] > field.cells[cell * f + k]) {
                fprintf(stderr, "kh_gpu_block_kernel: needs ascending cell rows\n");
                return 1;
            }
        }
    }
    bx.q = q; bx.f = f; bx.n = n;
    bx.nblocks_dp = nblocks; bx.bcoset = bcoset; bx.bwidth = bwidth; bx.copies = bcopies; bx.bfirst = bfirst;
    bx.cells = field.cells;
    bx.salt = (uint32_t)salt;
    char name[128];
    snprintf(name, sizeof name, "%s", gpu.cuda.name);
    int code = solve_blocks(&bx, budget, block_requests, max_rounds, max_residual, polynomial, argv[4],
                            &parameters, candidate, t0, t_field, name);
    kh_resource_print("solver", 0);
    return code;
}
