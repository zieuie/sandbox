#define _GNU_SOURCE

// kh_gpu_wide_kernel: exact block matching for fields past kh_gpu_block_kernel's limits (F above
// 65,534, q up to 2^40, field rows larger than host memory). See docs/GPU_WIDE_MATCHING_PLAN.md.
// Same input and payload contract as kh_gpu_block_kernel; the differences are inside:
//  - choices are 32-bit on the GPU, and on the host they live in the payload file itself, packed
//    at the certificate's width and written in place block by block (no scratch copy);
//  - field rows are built in passes: one count walk, then one placement walk per group of blocks;
//  - requests a pass leaves unmatched are carried into later passes, then rescue passes.

#include "field_walk.h"
#include "kh_cuda.h"
#include "kh_field.h"
#include "kh_resource.h"

#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define FREE UINT32_MAX
#define NOCHOICE UINT32_MAX
#define EXTRA_MAX 256
#define MAX_ROUNDS_LIMIT 4096
#define SYNC_BYTES (UINT64_C(64) << 20)

extern const kh_cuda_image_t kh_wide_images[];
extern const unsigned kh_wide_images_count;

static void help(void) {
    puts("Exact GPU matching over one primitive-X field too wide for kh_gpu_block_kernel: F above 65534,\n"
         "q up to 2^40, or field rows larger than host memory. Blocks are matched one after another on one\n"
         "GPU; field rows are built in passes; choices are written in place into the payload.\n"
         "Usage: kh_gpu_wide_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr]\n"
         "       [--start N] [--threads N] [--max-bytes N] [--device N] [--salt N] [--row-bytes N]\n"
         "       [--block-device-bytes N] [--block-requests N] [--max-rounds N] [--max-residual N]\n"
         "       [--max-rescue-passes N]\n"
         "Example: printf '1\\n1 1\\n' > /tmp/blocks.txt\n"
         "         ./kh_gpu_wide_kernel 3 3 /tmp/blocks.txt /tmp/choices.bin\n"
         "Blocks file, payload and metadata JSON as kh_gpu_block_kernel. Exit 0: full matching (payload\n"
         "written); 4: incomplete (no payload); 1: error. PAYLOAD must not exist; it is removed unless the\n"
         "matching is complete. --row-bytes caps one pass's field rows (default: what --max-bytes leaves);\n"
         "--max-rounds caps exchange rounds per pass (1: none); --max-rescue-passes (default 4) caps the\n"
         "passes that revisit blocks with free rights after the last pass. p = 2 is refused (F is a power\n"
         "of two, so the payload has no spare value for 'unmatched'): use kh_gpu_block_kernel.\n"
         "--test-cells FILE (tests only) replaces the field with q raw u32 labels, ascending per cell.\n"
         "--label-split-bits B (tests only, default 32) stores labels as B low bits plus breakpoints.");
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
                        uint32_t **coset, uint32_t **width, uint32_t **copies_out, uint32_t *count, uint64_t *n) {
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
    // Cosets stay 32-bit (they are X-exponent offsets, and there are at most n/F of them); the
    // request count is 64-bit but at most q.
    if (valid && (cosets + 1 > parameters->q - 1 || cosets >= UINT32_MAX || fscanf(file, " %c", &trailing) == 1 ||
                  ferror(file) || stripes * parameters->f > parameters->q)) {
        valid = false;
    }
    fclose(file);
    *count = (uint32_t)blocks;
    *n = stripes * parameters->f;
    return valid;
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

// One progress line: matched requests overall, plus where the current stage is (field, blocks,
// exchange, write) so the bridge and dashboard can show every stage of a long run.
static void progress(uint64_t done, uint64_t total, const char *phase, const char *stage, uint64_t stage_done,
                     uint64_t stage_total) {
    printf("{\"done\":%" PRIu64 ",\"total\":%" PRIu64 ",\"checkpoint_done\":0,\"phase\":\"%s\",\"units\":\"requests\","
           "\"stage\":\"%s\",\"stage_done\":%" PRIu64 ",\"stage_total\":%" PRIu64 "}\n",
           done, total, phase, stage, stage_done, stage_total);
    fflush(stdout);
}



// ---------------------------------------------------------------------------------------------
// The payload as the choice store. Request u's choice occupies bits [u*bits, (u+1)*bits), low bit
// first, exactly as in the certificate; all ones (2^bits - 1, never a valid choice while F is not
// a power of two) means unmatched. Every request is written when its block first runs, before
// anything reads it, so the sparse file's zeros are never mistaken for choices. Single-threaded.
// ---------------------------------------------------------------------------------------------
typedef struct {
    int fd;
    uint32_t bits;
    uint64_t sentinel, n, bytes;
    uint64_t dirty;            // page bytes written since the last fdatasync
    uint8_t *buffer;
    size_t capacity;
} store_t;

static bool store_open(store_t *store, const char *path, uint64_t n, uint32_t bits) {
    memset(store, 0, sizeof *store);
    store->bits = bits;
    store->sentinel = (UINT64_C(1) << bits) - 1;
    store->n = n;
    store->bytes = (n * bits + 7) / 8;
    store->fd = open(path, O_RDWR | O_CREAT | O_EXCL, 0600);
    if (store->fd < 0) return false;
    return ftruncate(store->fd, (off_t)store->bytes) == 0;
}

static bool store_reserve(store_t *store, size_t bytes) {
    if (bytes <= store->capacity) return true;
    uint8_t *grown = realloc(store->buffer, bytes);
    if (grown == NULL) return false;
    store->buffer = grown;
    store->capacity = bytes;
    return true;
}

static bool full_pread(int fd, uint8_t *buffer, size_t length, uint64_t offset) {
    while (length) {
        ssize_t got = pread(fd, buffer, length, (off_t)offset);
        if (got <= 0) return false;
        buffer += got; length -= (size_t)got; offset += (uint64_t)got;
    }
    return true;
}

static bool full_pwrite(int fd, const uint8_t *buffer, size_t length, uint64_t offset) {
    while (length) {
        ssize_t put = pwrite(fd, buffer, length, (off_t)offset);
        if (put <= 0) return false;
        buffer += put; length -= (size_t)put; offset += (uint64_t)put;
    }
    return true;
}

// Choices of requests [first, first + count) into out (NOCHOICE where unmatched).
static bool store_read(store_t *store, uint64_t first, uint64_t count, uint32_t *out) {
    if (count == 0) return true;
    uint64_t bit = first * store->bits, end = bit + count * store->bits;
    uint64_t byte0 = bit >> 3, length = ((end + 7) >> 3) - byte0;
    if (end > store->n * store->bits || !store_reserve(store, length + 8) ||
        !full_pread(store->fd, store->buffer, length, byte0)) {
        return false;
    }
    const uint8_t *buffer = store->buffer;
    uint64_t accumulator = buffer[0] >> (bit & 7), mask = store->sentinel;
    uint32_t available = 8 - (uint32_t)(bit & 7);
    size_t at = 1;
    for (uint64_t index = 0; index < count; ++index) {
        while (available < store->bits) {
            accumulator |= (uint64_t)buffer[at++] << available;
            available += 8;
        }
        uint64_t value = accumulator & mask;
        out[index] = value == mask ? NOCHOICE : (uint32_t)value;
        accumulator >>= store->bits;
        available -= store->bits;
    }
    return true;
}

// Write choices of requests [first, first + count), keeping the neighbors' bits in shared edge bytes.
static bool store_write(store_t *store, uint64_t first, uint64_t count, const uint32_t *values) {
    if (count == 0) return true;
    uint32_t bits = store->bits;
    uint64_t bit = first * bits, end = bit + count * bits;
    uint64_t byte0 = bit >> 3, byte1 = (end + 7) >> 3, length = byte1 - byte0;
    uint32_t head = (uint32_t)(bit & 7), tail = (uint32_t)(end & 7);
    if (end > store->n * bits || !store_reserve(store, length + 8)) return false;
    uint8_t first_old = 0, last_old = 0;
    if (head && !full_pread(store->fd, &first_old, 1, byte0)) return false;
    if (tail && !full_pread(store->fd, &last_old, 1, byte1 - 1)) return false;
    uint8_t *buffer = store->buffer;
    uint64_t accumulator = first_old & ((1u << head) - 1);
    uint32_t available = head;
    size_t at = 0;
    for (uint64_t index = 0; index < count; ++index) {
        uint64_t value = values[index] == NOCHOICE ? store->sentinel : values[index];
        if (value >= store->sentinel && values[index] != NOCHOICE) return false;
        accumulator |= value << available;
        available += bits;
        while (available >= 8) {
            buffer[at++] = (uint8_t)accumulator;
            accumulator >>= 8;
            available -= 8;
        }
    }
    if (available) buffer[at++] = (uint8_t)(accumulator | (last_old & ~((1u << available) - 1)));
    if (at != length || !full_pwrite(store->fd, buffer, length, byte0)) return false;
    uint64_t page = 4096;
    store->dirty += ((byte1 - 1) / page - byte0 / page + 1) * page;
    // Write back in bounded steps: gigabytes of dirty pages once stalled the leader's fsyncs.
    if (store->dirty >= SYNC_BYTES) {
        store->dirty = 0;
        return fdatasync(store->fd) == 0;
    }
    return true;
}

// ---------------------------------------------------------------------------------------------
// Blocks, as in kh_gpu_block_kernel: block b owns the requests in its cell range and a contiguous
// window of right vertices; leftover requests are imported into blocks with free rights.
// ---------------------------------------------------------------------------------------------

// Mirrors bgraph_t in kernels.cu (same member order and widths).
typedef struct {
    kh_dptr_t rows, bp, lfirst, lcoset, lwidth, icoset, islot;
    uint64_t qm1, lo, hi;
    uint32_t nblocks, f, nown, n, nbp, lshift;
} device_bgraph_t;

typedef struct {
    uint32_t home;     // block that owns the request
    uint32_t li;       // its local index there
    uint32_t cursor;   // next candidate offset when it is looking for a block
} member_t;

typedef struct {
    member_t *items;
    size_t count, capacity;
} members_t;

static bool members_push(members_t *list, member_t item) {
    if (list->count == list->capacity) {
        size_t grown = list->capacity ? 2 * list->capacity : 1024;
        member_t *items = realloc(list->items, grown * sizeof *items);
        if (items == NULL) return false;
        list->items = items;
        list->capacity = grown;
    }
    list->items[list->count++] = item;
    return true;
}

typedef struct {
    uint32_t c_lo, c_hi, nseg;
    uint64_t m, rlo, rhi, matched;
    uint32_t *segj, *lfirst, *lcoset, *lwidth;
    uint32_t extra[EXTRA_MAX];
    uint32_t nextra;
    member_t *imp;
    size_t nimp;
    uint64_t imp_limit;
    bool ran;
} block_t;

typedef struct {
    gpu_t *gpu;
    void *greedy_w, *expand_w, *restore_w, *count_free, *check_w;
    const kh_parameters_t *parameters;
    uint64_t q, f, n;
    uint32_t nblocks_dp, *bcoset, *bwidth, *copies;
    uint64_t *bfirst;
    fw_walk_t *walk;           // count walk (real fields)
    const uint32_t *table;     // --test-cells labels (tests), instead of a walk
    uint32_t split, nbp;
    fw_rows_t rows;            // the current pass's rows
    store_t store;
    uint32_t salt, threads;
    block_t *blocks;
    uint32_t nblock;
    uint64_t phases, scans, matched;
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

// Position of a block's local request in canonical (payload) order: DP run j, its coset, cell.
static uint64_t canonical(const bctx_t *x, const block_t *blk, uint32_t li) {
    uint32_t lo = 0, hi = blk->nseg;
    while (lo + 1 < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (blk->lfirst[mid] <= li) lo = mid; else hi = mid;
    }
    uint32_t off = li - blk->lfirst[lo], j = blk->segj[lo];
    return x->bfirst[j] + (uint64_t)(off / blk->lwidth[lo]) * x->bwidth[j] + blk->c_lo + off % blk->lwidth[lo];
}

static uint32_t member_cell(const bctx_t *x, member_t m) {
    uint32_t coset, cell;
    member_request(&x->blocks[m.home], m.li, &coset, &cell);
    return cell;
}

// A block's own choices move as one run of consecutive cells per (DP run, coset).
static bool block_own_io(bctx_t *x, const block_t *blk, uint32_t *values, bool write) {
    for (uint32_t s = 0; s < blk->nseg; ++s) {
        uint32_t j = blk->segj[s];
        for (uint32_t copy = 0; copy < x->copies[j]; ++copy) {
            uint64_t first = x->bfirst[j] + (uint64_t)copy * x->bwidth[j] + blk->c_lo;
            uint32_t *run = values + blk->lfirst[s] + (uint64_t)copy * blk->lwidth[s];
            if (!(write ? store_write(&x->store, first, blk->lwidth[s], run)
                        : store_read(&x->store, first, blk->lwidth[s], run))) {
                return false;
            }
        }
    }
    return true;
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
// The first run of a block appends its unmatched own requests to `pending`.
static bool run_block(bctx_t *x, uint32_t b, const member_t *fresh, size_t nfresh, members_t *pending) {
    gpu_t *gpu = x->gpu;
    block_t *blk = &x->blocks[b];
    bool first = !blk->ran;
    uint32_t f = (uint32_t)x->f;
    size_t nimp = blk->nimp + nfresh;
    uint32_t nown = (uint32_t)blk->m;
    uint32_t n = nown + (uint32_t)nimp;
    uint32_t cells = blk->c_hi - blk->c_lo;
    uint64_t window = blk->rhi - blk->rlo;
    const fw_rows_t *rows = &x->rows;
    unsigned mark = gpu->allocated;
    uint32_t *icoset = malloc(nimp * 4 + 4), *islot = malloc(nimp * 4 + 4);
    uint32_t *hchoice = malloc((size_t)n * 4 + 4);
    uint64_t *where = malloc(nimp * 8 + 8);   // imports' canonical positions
    if (icoset == NULL || islot == NULL || hchoice == NULL || where == NULL) {
        gpu->error = "host allocation failed";
        free(icoset); free(islot); free(hchoice); free(where);
        return false;
    }
    uint32_t own_slot = rows->slot[blk->c_lo];
    if (own_slot == FW_NO_SLOT || rows->slot[blk->c_hi - 1] != own_slot + cells - 1) {
        gpu->error = "a block's rows are not in this pass";
    }
    if (gpu->error == NULL && !first && !block_own_io(x, blk, hchoice, false)) gpu->error = "payload read failed";
    for (size_t index = 0; gpu->error == NULL && index < nimp; ++index) {
        member_t m = index < blk->nimp ? blk->imp[index] : fresh[index - blk->nimp];
        const block_t *home = &x->blocks[m.home];
        uint32_t cell;
        member_request(home, m.li, &icoset[index], &cell);
        where[index] = canonical(x, home, m.li);
        if (!slot_of(blk, cell, &islot[index]) || rows->slot[cell] == FW_NO_SLOT) {
            gpu->error = "imported request has no row in this pass";
        } else if (!store_read(&x->store, where[index], 1, &hchoice[nown + index])) {
            gpu->error = "payload read failed";
        }
    }
    uint32_t slots = cells + blk->nextra;
    uint32_t nbp = rows->nbp;
    device_bgraph_t g = {0};
    g.nblocks = blk->nseg; g.f = f; g.qm1 = x->q - 1; g.nown = nown; g.n = n;
    g.lo = blk->rlo; g.hi = blk->rhi; g.nbp = nbp; g.lshift = rows->shift;
    g.rows = gpu_alloc(gpu, (uint64_t)slots * f * 4);
    g.bp = gpu_alloc(gpu, (uint64_t)slots * nbp * 4);
    g.lfirst = gpu_alloc(gpu, blk->nseg * 4);
    g.lcoset = gpu_alloc(gpu, blk->nseg * 4);
    g.lwidth = gpu_alloc(gpu, blk->nseg * 4);
    g.icoset = gpu_alloc(gpu, nimp * 4);
    g.islot = gpu_alloc(gpu, nimp * 4);
    kh_dptr_t left = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t choice = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t right = gpu_alloc(gpu, window * 4);
    kh_dptr_t root = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t parent = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t viak = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t queue_a = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t queue_b = gpu_alloc(gpu, (uint64_t)n * 4);
    kh_dptr_t counters = gpu_alloc(gpu, 64);
    kh_dptr_t scans = counters + 32;
    if (gpu->error == NULL) {
        gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.rows, rows->rows + (uint64_t)own_slot * f, (uint64_t)cells * f * 4), "upload rows");
        if (nbp != 0) {
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.bp, rows->bp + (uint64_t)own_slot * nbp, (uint64_t)cells * nbp * 4),
                   "upload breakpoints");
        }
        for (uint32_t index = 0; gpu->error == NULL && index < blk->nextra; ++index) {
            uint32_t slot = rows->slot[blk->extra[index]];
            if (slot == FW_NO_SLOT) {
                gpu->error = "an imported request's row is not in this pass";
                break;
            }
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.rows + ((uint64_t)cells + index) * f * 4,
                                               rows->rows + (uint64_t)slot * f, (uint64_t)f * 4), "upload rows");
            if (nbp != 0) {
                gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(g.bp + ((uint64_t)cells + index) * nbp * 4,
                                                   rows->bp + (uint64_t)slot * nbp, (uint64_t)nbp * 4),
                       "upload breakpoints");
            }
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
            gpu_ok(gpu, gpu->cuda.cuMemsetD32(choice, NOCHOICE, n), "init");
        } else {
            gpu_ok(gpu, gpu->cuda.cuMemcpyHtoD(choice, hchoice, (uint64_t)n * 4), "upload choices");
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
        kh_dptr_t end_k = gpu_alloc(gpu, (uint64_t)nroots * 4);
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
        gpu_ok(gpu, gpu->cuda.cuMemcpyDtoH(hchoice, choice, (uint64_t)n * 4), "download");
    }
    gpu_free_last(gpu, mark);
    free(icoset);
    free(islot);
    if (gpu->error == NULL && !block_own_io(x, blk, hchoice, true)) gpu->error = "payload write failed";
    member_t *kept = gpu->error == NULL ? malloc((nimp + 1) * sizeof *kept) : NULL;
    if (gpu->error == NULL && kept == NULL) gpu->error = "host allocation failed";
    size_t count = 0;
    for (size_t index = 0; gpu->error == NULL && index < nimp; ++index) {
        member_t m = index < blk->nimp ? blk->imp[index] : fresh[index - blk->nimp];
        if (!store_write(&x->store, where[index], 1, &hchoice[nown + index])) {
            gpu->error = "payload write failed";
        } else if (hchoice[nown + index] != NOCHOICE) {
            kept[count++] = m;
        }
    }
    for (uint32_t li = 0; gpu->error == NULL && first && li < nown; ++li) {
        if (hchoice[li] == NOCHOICE && !members_push(pending, (member_t){b, li, 0})) gpu->error = "host allocation failed";
    }
    free(where);
    free(hchoice);
    if (gpu->error != NULL) {
        free(kept);
        return false;
    }
    free(blk->imp);
    blk->imp = kept;
    blk->nimp = count;
    x->matched += (uint64_t)checked - blk->matched;
    blk->matched = checked;
    blk->ran = true;
    return true;
}

// Device bytes for a block; mirrors block_cost() in kh_gpu_block_kernel with 4-byte choices.
static uint64_t block_cost(uint64_t f, uint64_t nbp, uint64_t cells, uint64_t m, uint64_t window) {
    uint64_t imports = m / 64 + 64;
    return 4 * (f + nbp) * (cells + EXTRA_MAX) + 35 * (m + imports) + 4 * window + UINT64_C(16777216);
}

// Cut the used cells into `count` blocks of nearly equal request counts. Returns false if any
// block is over budget. Spare rights (n < q) go to the last window.
static bool cut_blocks(bctx_t *x, uint32_t count, uint64_t budget, const uint64_t *per_cell, uint64_t ncells,
                       uint32_t *bounds) {
    uint64_t f = x->f, total = 0;
    for (uint64_t c = 0; c < ncells; ++c) total += per_cell[c];
    uint64_t c = 0, cumulative = 0;
    for (uint32_t k = 0; k < count; ++k) {
        uint64_t target = (uint64_t)((__uint128_t)total * (k + 1) / count);
        uint64_t start = c, m = 0;
        while (c < ncells && (k + 1 == count || c == start || cumulative + per_cell[c] / 2 < target) &&
               (k + 1 == count || ncells - c > count - k - 1)) {
            cumulative += per_cell[c];
            m += per_cell[c];
            ++c;
        }
        uint64_t window = m + (k + 1 == count ? x->q - x->n : 0);
        // Local request and right indices are 32-bit, below the FREE/RESERVED/INACTIVE sentinels.
        if (c == start || m + m / 64 + 64 >= UINT32_MAX - 3 || window >= UINT32_MAX - 3 ||
            block_cost(f, x->nbp, c - start, m, window) > budget) {
            return false;
        }
        bounds[k] = (uint32_t)c;
    }
    return c == ncells;
}

static bool layout_blocks(bctx_t *x, uint64_t budget, uint64_t max_requests, const char **error) {
    uint64_t f = x->f, amax = 0;
    for (uint32_t j = 0; j < x->nblocks_dp; ++j) {
        if (x->bwidth[j] / f > amax) amax = x->bwidth[j] / f;
    }
    uint64_t ncells = amax * f;
    uint64_t *per_cell = calloc(ncells, sizeof *per_cell);
    uint32_t *bounds = NULL;
    if (per_cell == NULL) {
        *error = "host allocation failed";
        return false;
    }
    for (uint32_t j = 0; j < x->nblocks_dp; ++j) {
        for (uint64_t c = 0; c < x->bwidth[j]; ++c) per_cell[c] += x->copies[j];
    }
    uint64_t count = max_requests ? (x->n + max_requests - 1) / max_requests : 1;
    if (count == 0) count = 1;
    if (count > ncells) count = ncells;
    for (;; ++count) {
        if (count > ncells || count >= UINT32_MAX) {
            free(per_cell); free(bounds);
            *error = "no block layout fits the device budget";
            return false;
        }
        uint32_t *grown = realloc(bounds, count * 4);
        if (grown == NULL) {
            free(per_cell); free(bounds);
            *error = "host allocation failed";
            return false;
        }
        bounds = grown;
        if (cut_blocks(x, (uint32_t)count, budget, per_cell, ncells, bounds)) break;
    }
    x->blocks = calloc(count, sizeof *x->blocks);
    if (x->blocks == NULL) {
        free(per_cell); free(bounds);
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
        if (!blk->segj || !blk->lfirst || !blk->lcoset || !blk->lwidth) {
            free(per_cell); free(bounds);
            *error = "host allocation failed";
            return false;
        }
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

static int compare_u64(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left, b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

static uint64_t unique_sorted(uint64_t *values, uint64_t count) {
    qsort(values, count, sizeof *values, compare_u64);
    uint64_t kept = 0;
    for (uint64_t index = 0; index < count; ++index) {
        if (kept == 0 || values[kept - 1] != values[index]) values[kept++] = values[index];
    }
    return kept;
}

typedef struct {
    uint64_t done_base, total;
    char phase[64];
} pass_progress_t;

static void walk_progress(uint64_t done, uint64_t total, void *context) {
    (void)total;
    const pass_progress_t *pass = context;
    progress(pass->done_base, pass->total, pass->phase, "field", done, total);
}

// This pass's rows: from the count walk, or (tests) from the --test-cells table.
static bool build_rows(bctx_t *x, const uint64_t *cells, uint64_t ncells, pass_progress_t *context, const char **error) {
    fw_free(&x->rows);
    if (x->table == NULL) {
        return fw_walk_rows(x->walk, cells, ncells, walk_progress, context, &x->rows, error);
    }
    uint32_t f = (uint32_t)x->f, nbp = x->nbp, split = x->split;
    if (!fw_rows_alloc(&x->rows, x->parameters->budget, f, nbp, split, ncells)) {
        *error = "host allocation failed";
        return false;
    }
    uint32_t mask = split == 32 ? UINT32_MAX : (UINT32_C(1) << split) - 1;
    for (uint64_t slot = 0; slot < ncells; ++slot) {
        const uint32_t *row = x->table + cells[slot] * f;
        x->rows.slot[cells[slot]] = (uint32_t)slot;
        for (uint32_t k = 0; k < f; ++k) x->rows.rows[slot * f + k] = row[k] & mask;
        for (uint32_t high = 1; high <= nbp; ++high) {
            uint32_t k = 0;
            while (k < f && row[k] < ((uint64_t)high << split)) ++k;
            x->rows.bp[slot * nbp + high - 1] = k;
        }
    }
    return true;
}

typedef struct {
    uint64_t imports, matched, pass;
    double seconds;
} round_t;

typedef struct {
    round_t rounds[MAX_ROUNDS_LIMIT * 4];
    uint64_t round_count;          // exchange rounds so far (round 1, the blocks' first runs, is not counted)
    uint64_t *trace;               // unmatched after: start, each block's first run, each exchange round
    uint64_t trace_count, trace_capacity;
} log_t;

static bool trace_push(log_t *log, uint64_t value) {
    if (log->trace_count == log->trace_capacity) {
        uint64_t grown = log->trace_capacity ? 2 * log->trace_capacity : 1024;
        uint64_t *trace = realloc(log->trace, grown * sizeof *trace);
        if (trace == NULL) return false;
        log->trace = trace;
        log->trace_capacity = grown;
    }
    log->trace[log->trace_count++] = value;
    return true;
}

// Exchange rounds among the blocks marked in `target`, for pending requests whose rows this pass
// holds. Returns false on error; pending keeps the requests still unmatched.
static bool exchange(bctx_t *x, const bool *target, members_t *pending, uint64_t max_rounds, uint64_t pass,
                     log_t *log) {
    gpu_t *gpu = x->gpu;
    uint32_t pn = x->nblock;
    member_t **fresh = calloc(pn, sizeof *fresh);
    size_t *nfresh = calloc(pn, sizeof *nfresh);
    uint64_t *cap = calloc(pn, sizeof *cap);
    bool ok = fresh != NULL && nfresh != NULL && cap != NULL;
    for (uint64_t round = 1; ok && pending->count != 0 && round < max_rounds; ++round) {
        double started = now();
        for (uint32_t b = 0; b < pn; ++b) {
            nfresh[b] = 0;
            cap[b] = target[b] ? (x->blocks[b].rhi - x->blocks[b].rlo) - x->blocks[b].matched : 0;
        }
        uint64_t assigned = 0;
        for (size_t i = 0; ok && i < pending->count; ++i) {
            member_t *u = &pending->items[i];
            uint32_t cell = member_cell(x, *u);
            if (x->rows.slot[cell] == FW_NO_SLOT) continue;   // its row waits for a later pass
            for (uint32_t attempt = 0; pn > 1 && attempt < pn - 1; ++attempt) {
                uint32_t b = (u->home + 1 + (u->cursor++ % (pn - 1))) % pn;
                block_t *blk = &x->blocks[b];
                uint32_t slot;
                if (cap[b] == 0 || blk->nimp + nfresh[b] >= blk->imp_limit) continue;
                if (!slot_of(blk, cell, &slot)) {
                    if (blk->nextra == EXTRA_MAX) continue;
                    blk->extra[blk->nextra++] = cell;
                }
                if (fresh[b] == NULL && (fresh[b] = malloc(blk->imp_limit * sizeof **fresh)) == NULL) {
                    ok = false;
                    gpu->error = "host allocation failed";
                    break;
                }
                --cap[b];
                fresh[b][nfresh[b]++] = *u;
                ++assigned;
                break;
            }
        }
        if (!ok || assigned == 0) break;
        for (uint32_t b = 0; ok && b < pn; ++b) {
            if (nfresh[b] != 0) ok = run_block(x, b, fresh[b], nfresh[b], NULL);
        }
        size_t kept = 0;
        for (size_t i = 0; ok && i < pending->count; ++i) {
            member_t m = pending->items[i];
            uint32_t value;
            if (!store_read(&x->store, canonical(x, &x->blocks[m.home], m.li), 1, &value)) {
                ok = false;
                gpu->error = "payload read failed";
            } else if (value == NOCHOICE) {
                pending->items[kept++] = m;
            }
        }
        if (!ok) break;
        pending->count = kept;
        if (log->round_count < sizeof log->rounds / sizeof *log->rounds) {
            log->rounds[log->round_count] = (round_t){assigned, x->matched, pass, now() - started};
        }
        ++log->round_count;
        ok = trace_push(log, x->n - x->matched);
        if (!ok) gpu->error = "host allocation failed";
        fprintf(stderr, "pass %" PRIu64 " exchange round %" PRIu64 " imports=%" PRIu64 " pending=%zu seconds=%.3f\n",
                pass, round, assigned, kept, now() - started);
        progress(x->matched, x->n, "exchange", "exchange", round, max_rounds - 1);
    }
    for (uint32_t b = 0; fresh != NULL && b < pn; ++b) free(fresh[b]);
    free(fresh); free(nfresh); free(cap);
    if (!ok && gpu->error == NULL) gpu->error = "host allocation failed";
    return ok;
}

// Cells of pending requests that aren't in [lo, hi), appended to cells while they fit `room`.
static uint64_t add_pending_cells(const bctx_t *x, const members_t *pending, uint64_t lo, uint64_t hi,
                                  uint64_t *cells, uint64_t count, uint64_t room) {
    uint64_t start = count;
    for (size_t i = 0; i < pending->count && count - start < room; ++i) {
        uint64_t cell = member_cell(x, pending->items[i]);
        if (cell < lo || cell >= hi) cells[count++] = cell;
    }
    return count;
}

typedef struct {
    uint64_t max_requests, max_rounds, max_residual, max_rescue, row_budget;
} controls_t;

typedef struct {
    double field, rows, blocks, exchange, check;
    uint64_t passes, rescue_passes, residual1;
} totals_t;

// Most cells one pass may hold under the row budget.
static uint64_t pass_cell_room(const bctx_t *x, uint64_t row_budget) {
    uint64_t fixed = fw_rows_bytes(x->parameters, x->threads, x->split, 0);
    uint64_t per_cell = fw_rows_bytes(x->parameters, x->threads, x->split, 1) - fixed;
    return row_budget > fixed ? (row_budget - fixed) / per_cell : 0;
}

// Round 1 pass by pass, with exchange inside each pass, then rescue passes over blocks that still
// have free rights. Returns 0 complete, 4 incomplete, 1 error.
static int match_passes(bctx_t *x, const controls_t *controls, log_t *log, totals_t *totals, members_t *pending) {
    gpu_t *gpu = x->gpu;
    uint32_t pn = x->nblock;
    uint64_t room = pass_cell_room(x, controls->row_budget);
    uint64_t *cells = malloc((room + 1) * sizeof *cells);
    bool *target = calloc(pn, sizeof *target), *considered = calloc(pn, sizeof *considered);
    const char *error = NULL;
    int code = 1;
    if (cells == NULL || target == NULL || considered == NULL) {
        fprintf(stderr, "kh_gpu_wide_kernel: host allocation failed\n");
        goto done;
    }
    for (uint32_t b = 0; b < pn; ++b) {
        if (x->blocks[b].c_hi - x->blocks[b].c_lo > room) {
            fprintf(stderr, "kh_gpu_wide_kernel: one block's rows exceed the row budget (%" PRIu64 " bytes)\n",
                    controls->row_budget);
            goto done;
        }
    }
    // Plan: consecutive blocks while their cells fit 15/16 of the room (the rest is for carried cells).
    uint32_t *pass_start = malloc(((uint64_t)pn + 1) * sizeof *pass_start), npasses = 0;
    if (pass_start == NULL) goto done;
    for (uint32_t b = 0; b < pn;) {
        uint64_t held = 0;
        pass_start[npasses++] = b;
        do {
            held += x->blocks[b].c_hi - x->blocks[b].c_lo;
            ++b;
        } while (b < pn && held + (x->blocks[b].c_hi - x->blocks[b].c_lo) <= room - room / 16);
    }
    pass_start[npasses] = pn;
    totals->passes = npasses;
    fprintf(stderr, "blocks=%u passes=%u cells_per_pass<=%" PRIu64 "\n", pn, npasses, room);
    bool stuck = false;
    for (uint32_t pass = 0; pass < npasses && !stuck; ++pass) {
        uint32_t b0 = pass_start[pass], b1 = pass_start[pass + 1];
        uint64_t lo = x->blocks[b0].c_lo, hi = x->blocks[b1 - 1].c_hi, count = 0;
        for (uint64_t cell = lo; cell < hi; ++cell) cells[count++] = cell;
        count = unique_sorted(cells, add_pending_cells(x, pending, lo, hi, cells, count, room - count));
        pass_progress_t context = {x->matched, x->n, ""};
        snprintf(context.phase, sizeof context.phase, "pass %u/%u rows", pass + 1, npasses);
        double started = now();
        if (!build_rows(x, cells, count, &context, &error)) {
            fprintf(stderr, "kh_gpu_wide_kernel: %s\n", error);
            free(pass_start);
            goto done;
        }
        totals->rows += now() - started;
        started = now();
        size_t before = pending->count;
        for (uint32_t b = b0; b < b1; ++b) {
            if (!run_block(x, b, NULL, 0, pending)) {
                fprintf(stderr, "kh_gpu_wide_kernel: %s\n", gpu->error);
                free(pass_start);
                goto done;
            }
            char phase[64];
            snprintf(phase, sizeof phase, "block %u/%u", b + 1, pn);
            progress(x->matched, x->n, phase, "blocks", b + 1, pn);
            fprintf(stderr, "block %u/%u cells=[%u,%u) requests=%" PRIu64 " matched=%" PRIu64 "\n",
                    b + 1, pn, x->blocks[b].c_lo, x->blocks[b].c_hi, x->blocks[b].m, x->blocks[b].matched);
            if (!trace_push(log, x->n - x->matched)) goto done;
        }
        totals->blocks += now() - started;
        totals->residual1 += pending->count - before;
        fprintf(stderr, "pass %u/%u blocks [%u,%u) cells=%" PRIu64 " round-1 residual=%zu pending=%zu\n",
                pass + 1, npasses, b0, b1, count, pending->count - before, pending->count);
        if (pending->count > controls->max_residual) {
            stuck = true;   // a hopeless field: don't spend the remaining walks on it
            break;
        }
        memset(target, 0, pn * sizeof *target);
        for (uint32_t b = b0; b < b1; ++b) target[b] = true;
        started = now();
        if (!exchange(x, target, pending, controls->max_rounds, pass + 1, log)) {
            fprintf(stderr, "kh_gpu_wide_kernel: %s\n", gpu->error);
            free(pass_start);
            goto done;
        }
        totals->exchange += now() - started;
    }
    free(pass_start);
    // Rescue passes: blocks with free rights, most free first, plus the pending requests' rows.
    for (uint64_t rescue = 0; !stuck && pending->count != 0 && controls->max_rounds > 1 &&
                              rescue < controls->max_rescue; ++rescue) {
        uint64_t count = add_pending_cells(x, pending, 0, 0, cells, 0, room / 4);
        memset(target, 0, pn * sizeof *target);
        memset(considered, 0, pn * sizeof *considered);
        uint32_t targets = 0;
        for (;;) {
            uint32_t best = pn;
            uint64_t best_free = 0;
            for (uint32_t b = 0; b < pn; ++b) {
                uint64_t free_rights = (x->blocks[b].rhi - x->blocks[b].rlo) - x->blocks[b].matched;
                if (!considered[b] && free_rights > best_free) { best = b; best_free = free_rights; }
            }
            if (best == pn) break;
            considered[best] = true;
            block_t *blk = &x->blocks[best];
            // Its own rows, and the rows of requests it already imported (its extra list).
            uint64_t need = (blk->c_hi - blk->c_lo) + blk->nextra;
            if (count + need > room) continue;
            for (uint64_t cell = blk->c_lo; cell < blk->c_hi; ++cell) cells[count++] = cell;
            for (uint32_t index = 0; index < blk->nextra; ++index) cells[count++] = blk->extra[index];
            target[best] = true;
            ++targets;
        }
        if (targets == 0) break;
        count = unique_sorted(cells, count);
        pass_progress_t context = {x->matched, x->n, ""};
        snprintf(context.phase, sizeof context.phase, "rescue pass %" PRIu64 " rows", rescue + 1);
        double started = now();
        if (!build_rows(x, cells, count, &context, &error)) {
            fprintf(stderr, "kh_gpu_wide_kernel: %s\n", error);
            goto done;
        }
        totals->rows += now() - started;
        ++totals->rescue_passes;
        size_t before = pending->count;
        started = now();
        if (!exchange(x, target, pending, controls->max_rounds, totals->passes + rescue + 1, log)) {
            fprintf(stderr, "kh_gpu_wide_kernel: %s\n", gpu->error);
            goto done;
        }
        totals->exchange += now() - started;
        fprintf(stderr, "rescue pass %" PRIu64 ": %u blocks, pending %zu -> %zu\n", rescue + 1, targets, before,
                pending->count);
        if (pending->count == before) break;
    }
    code = pending->count == 0 ? 0 : 4;
done:
    fw_free(&x->rows);
    free(cells);
    free(target);
    free(considered);
    return code;
}

// Every request has a choice below F and the padding after the last one is zero.
static bool check_payload(bctx_t *x) {
    uint64_t step = UINT64_C(1) << 22, n = x->n;
    uint32_t *values = malloc(step * sizeof *values);
    bool ok = values != NULL;
    for (uint64_t first = 0; ok && first < n; first += step) {
        uint64_t count = first + step < n ? step : n - first;
        ok = store_read(&x->store, first, count, values);
        for (uint64_t index = 0; ok && index < count; ++index) ok = values[index] < x->f;
        if (first / step % 16 == 0) progress(n, n, "checking payload", "check", first + count, n);
    }
    free(values);
    uint32_t tail = (uint32_t)((n * x->store.bits) & 7);
    uint8_t last = 0;
    if (ok && tail) ok = full_pread(x->store.fd, &last, 1, x->store.bytes - 1) && (last >> tail) == 0;
    return ok && fsync(x->store.fd) == 0;
}

static void field_progress(uint64_t done, uint64_t total, void *context) {
    progress(0, *(const uint64_t *)context, "field", "field", done, total);
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
    uint64_t block_budget = 0, split_bits = 32, row_bytes = 0;
    controls_t controls = {0, 16, UINT64_MAX, 4, 0};
    const char *poly_text = NULL, *test_cells = NULL, *error = NULL;
    kh_parameters_t parameters;

    if (!kh_parse_u64(argv[1], &p) || !kh_parse_u64(argv[2], &r) || p > UINT32_MAX || r > UINT32_MAX ||
        !kh_parameters_dp64((uint32_t)p, (uint32_t)r, &parameters, &error) || parameters.q >= FW_MAX_Q) {
        fprintf(stderr, "kh_gpu_wide_kernel: invalid dimensions (q must be below 2^40)\n");
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
            fprintf(stderr, "kh_gpu_wide_kernel: invalid value for %s\n", option);
            return 1;
        }
        if (!strcmp(option, "--threads")) threads = value;
        else if (!strcmp(option, "--start")) start = value;
        else if (!strcmp(option, "--max-bytes")) maximum = value;
        else if (!strcmp(option, "--device")) device = value;
        else if (!strcmp(option, "--salt")) salt = value;
        else if (!strcmp(option, "--block-device-bytes")) block_budget = value;
        else if (!strcmp(option, "--block-requests")) controls.max_requests = value;
        else if (!strcmp(option, "--max-rounds")) controls.max_rounds = value;
        else if (!strcmp(option, "--max-residual")) controls.max_residual = value;
        else if (!strcmp(option, "--max-rescue-passes")) controls.max_rescue = value;
        else if (!strcmp(option, "--row-bytes")) row_bytes = value;
        else if (!strcmp(option, "--label-split-bits")) split_bits = value;
        else {
            fprintf(stderr, "kh_gpu_wide_kernel: unsupported option %s\n", option);
            return 1;
        }
    }
    uint32_t bits = 0;
    for (uint64_t value = parameters.f - 1; value != 0; value >>= 1) ++bits;
    if (threads == 0 || threads > 1024 || start >= parameters.q || maximum == 0 || device > 64 ||
        salt > UINT32_MAX || controls.max_rounds == 0 || controls.max_rounds > MAX_ROUNDS_LIMIT ||
        controls.max_rescue > 64 || split_bits == 0 || split_bits > 32 ||
        ((parameters.q - 1) >> split_bits) > FW_MAX_BREAKPOINTS ||
        (test_cells != NULL && parameters.q > UINT32_MAX)) {
        fprintf(stderr, "kh_gpu_wide_kernel: invalid resource controls, or --max-rounds outside 1..%d\n",
                MAX_ROUNDS_LIMIT);
        return 1;
    }
    if (parameters.f == (UINT64_C(1) << bits) || parameters.f > UINT32_MAX - 1) {
        fprintf(stderr, "kh_gpu_wide_kernel: F = %u leaves no spare payload value for 'unmatched' "
                "(p = 2); use kh_gpu_block_kernel\n", parameters.f);
        return 1;
    }
    uint64_t *bfirst = NULL, n = 0;
    uint32_t *bcoset = NULL, *bwidth = NULL, *bcopies = NULL, nblocks = 0;
    if (!blocks_read(argv[3], &parameters, &bfirst, &bcoset, &bwidth, &bcopies, &nblocks, &n)) {
        fprintf(stderr, "kh_gpu_wide_kernel: invalid request blocks\n");
        return 1;
    }
    uint64_t q = parameters.q, f = parameters.f;
    if (controls.max_residual == UINT64_MAX) {
        controls.max_residual = n / 1000 + 16;
    }

    double t0 = now();
    gpu_t gpu = {0};
    if (!kh_cuda_open(&gpu.cuda, (int)device, kh_wide_images, kh_wide_images_count, &error)) {
        fprintf(stderr, "kh_gpu_wide_kernel: %s\n", error);
        return 1;
    }
    size_t free_bytes = 0, total_bytes = 0;
    gpu.cuda.cuMemGetInfo(&free_bytes, &total_bytes);
    uint64_t budget = block_budget;
    if (budget == 0) {
        budget = free_bytes > UINT64_C(268435456) ? free_bytes - UINT64_C(268435456) : 0;
    }
    // Everything but the pass rows: the count walk's offsets (or the test table), per-block staging
    // (at most budget/8: a block's requests cost the device at least 35 bytes each and the host
    // about 4), pending requests, and fixed overhead. Pass rows get the rest unless --row-bytes.
    uint64_t pending_cap = controls.max_residual < n ? controls.max_residual : n;
    uint64_t fixed = (test_cells ? 4 * q : fw_walk_bytes(&parameters, (uint32_t)threads, (uint32_t)split_bits)) +
                     budget / 8 + 12 * pending_cap + UINT64_C(8388608) * threads + UINT64_C(268435456);
    controls.row_budget = row_bytes ? row_bytes : (maximum > fixed ? maximum - fixed : 0);
    if (fixed + controls.row_budget > maximum || controls.row_budget == 0) {
        fprintf(stderr, "kh_gpu_wide_kernel: requires at least %" PRIu64 " host bytes plus pass rows; limit=%" PRIu64 "\n",
                fixed, maximum);
        kh_cuda_close(&gpu.cuda);
        return 1;
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
        fprintf(stderr, "kh_gpu_wide_kernel: embedded module lacks kernels\n");
        kh_cuda_close(&gpu.cuda);
        return 1;
    }

    uint16_t polynomial[32] = {0};
    uint64_t candidate = 0;
    if (poly_text != NULL) {
        if (!polynomial_parse(poly_text, &parameters, polynomial) || !fw_primitive(&parameters, polynomial)) {
            fprintf(stderr, "kh_gpu_wide_kernel: polynomial is not primitive with generator X\n");
            return 1;
        }
    } else if (!fw_generate(&parameters, start, polynomial, &candidate)) {
        fprintf(stderr, "kh_gpu_wide_kernel: primitive polynomial candidates exhausted\n");
        return 1;
    }
    bx.parameters = &parameters;
    bx.q = q; bx.f = f; bx.n = n;
    bx.nblocks_dp = nblocks; bx.bcoset = bcoset; bx.bwidth = bwidth; bx.copies = bcopies; bx.bfirst = bfirst;
    bx.split = (uint32_t)split_bits;
    bx.nbp = (uint32_t)((q - 1) >> split_bits);
    bx.salt = (uint32_t)salt;
    bx.threads = (uint32_t)threads;
    totals_t totals = {0};
    progress(0, n, "field", "field", 0, q - 1);
    if (test_cells != NULL) {
        // Test-only: q raw little-endian u32 labels replace the field (synthetic deficient graphs).
        FILE *file = fopen(test_cells, "rb");
        uint32_t *table = malloc(q * 4);
        bool loaded = file != NULL && table != NULL && fread(table, 4, q, file) == q;
        if (file != NULL) fclose(file);
        for (uint64_t index = 0; loaded && index < q; ++index) loaded = table[index] < q;
        // Rows must ascend (real fields do by construction).
        for (uint64_t index = 0; loaded && index + 1 < q - q % f; ++index) {
            loaded = (index + 1) % f == 0 || table[index] <= table[index + 1];
        }
        if (!loaded) {
            fprintf(stderr, "kh_gpu_wide_kernel: invalid --test-cells table (labels below q, ascending rows)\n");
            return 1;
        }
        bx.table = table;
    } else {
        bx.walk = fw_walk_prepare(&parameters, polynomial, (uint32_t)threads, (uint32_t)split_bits, field_progress, &n, &error);
        if (bx.walk == NULL) {
            fprintf(stderr, "kh_gpu_wide_kernel: %s\n", error);
            return 1;
        }
    }
    totals.field = now() - t0;
    if (!layout_blocks(&bx, budget, controls.max_requests, &error)) {
        fprintf(stderr, "kh_gpu_wide_kernel: %s\n", error);
        return 1;
    }
    if (!store_open(&bx.store, argv[4], n, bits)) {
        fprintf(stderr, "kh_gpu_wide_kernel: cannot create the payload (it must not exist)\n");
        return 1;
    }
    log_t *log = calloc(1, sizeof *log);
    members_t pending = {0};
    if (log == NULL || !trace_push(log, n)) {
        unlink(argv[4]);
        fprintf(stderr, "kh_gpu_wide_kernel: host allocation failed\n");
        return 1;
    }
    double t_match = now();
    progress(0, n, "block 0/0", "blocks", 0, bx.nblock);
    int code = match_passes(&bx, &controls, log, &totals, &pending);
    char device_name[128];
    snprintf(device_name, sizeof device_name, "%s", gpu.cuda.name);
    gpu_free_last(&gpu, 0);
    kh_cuda_close(&gpu.cuda);
    double t_solve = now();
    if (code == 0) {
        if (!check_payload(&bx)) {
            fprintf(stderr, "kh_gpu_wide_kernel: final payload check failed\n");
            code = 1;
        }
    }
    totals.check = now() - t_solve;
    close(bx.store.fd);
    if (code != 0) unlink(argv[4]);
    if (code == 1) return 1;
    uint64_t imported = 0;
    for (uint32_t b = 0; b < bx.nblock; ++b) imported += bx.blocks[b].nimp;
    bool complete = code == 0;
    printf("{\"p\":%u,\"r\":%u,\"candidate\":%" PRIu64 ",\"polynomial\":[", parameters.p, parameters.r, candidate);
    for (uint32_t index = 0; index <= parameters.r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    printf("],\"status\":%u,\"required\":%" PRIu64 ",\"matched\":%" PRIu64 ",\"phases\":%" PRIu64
           ",\"scans\":%" PRIu64 ",\"hall_left\":0,\"hall_right\":0,\"engine\":\"gpu-wide\",\"device\":\"%s\","
           "\"blocks\":%u,\"passes\":%" PRIu64 ",\"rescue_passes\":%" PRIu64 ",\"row_bytes\":%" PRIu64
           ",\"rounds\":%" PRIu64 ",\"residual_round1\":%" PRIu64 ",\"residual\":%zu"
           ",\"imported\":%" PRIu64 ",\"incomplete\":%s,\"round_log\":[",
           complete ? 0u : 2u, n, bx.matched, bx.phases, bx.scans, device_name, bx.nblock, totals.passes,
           totals.rescue_passes, controls.row_budget, log->round_count + 1, totals.residual1, pending.count,
           imported, complete ? "false" : "true");
    uint64_t logged = log->round_count < sizeof log->rounds / sizeof *log->rounds ? log->round_count
                                                                                  : sizeof log->rounds / sizeof *log->rounds;
    for (uint64_t index = 0; index < logged; ++index) {
        const round_t *round = &log->rounds[index];
        printf("%s{\"round\":%" PRIu64 ",\"pass\":%" PRIu64 ",\"imports\":%" PRIu64 ",\"matched\":%" PRIu64
               ",\"seconds\":%.3f}", index == 0 ? "" : ",", index + 2, round->pass, round->imports, round->matched,
               round->seconds);
    }
    printf("],\"trace\":[");
    for (uint64_t k = 0; k < log->trace_count; ++k) {
        printf("%s[%" PRIu64 ",%" PRIu64 "]", k == 0 ? "" : ",", k, log->trace[k]);
    }
    printf("],\"seconds\":{\"setup\":0.0,\"field\":%.3f,\"rows\":%.3f,\"blocks\":%.3f,\"exchange\":%.3f,"
           "\"output\":%.3f,\"total\":%.3f}}\n",
           totals.field, totals.rows, totals.blocks, totals.exchange, totals.check, now() - t0);
    (void)t_match;
    fflush(stdout);
    fw_walk_free(bx.walk);
    kh_resource_print("solver", 0);
    return code;
}
