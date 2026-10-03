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

extern const kh_cuda_image_t kh_match_images[];
extern const unsigned kh_match_images_count;

// Mirrors graph_t in kernels.cu (pointers are 8-byte device addresses).
typedef struct {
    kh_dptr_t cells;
    kh_dptr_t bfirst;
    kh_dptr_t bcoset;
    kh_dptr_t bwidth;
    uint32_t nblocks;
    uint32_t f;
    uint32_t qm1;
    uint32_t n;
} device_graph_t;

static void help(void) {
    puts("Exact GPU matching kernel over one primitive-X field (CUDA driver API).\n"
         "Usage: kh_gpu_match_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr]\n"
         "       [--start N] [--threads N] [--max-bytes N] [--device N]\n"
         "       [--max-device-bytes N] [--salt N]\n"
         "Example: printf '1\\n1 1\\n' > /tmp/blocks.txt\n"
         "         ./kh_gpu_match_kernel 3 3 /tmp/blocks.txt /tmp/choices.bin\n"
         "Blocks, payload, metadata JSON and exit codes match kh_match_kernel:\n"
         "Exit 0: full matching; 2: exact obstruction (Hall bits appended); 1: error.\n"
         "--threads only affects CPU field construction. Checkpoints are not supported\n"
         "(complete runs take seconds). Use kh_cuda_probe to list devices.\n"
         "--test-cells FILE (tests only) replaces the field with q raw u32 labels.");
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
                        uint32_t **coset, uint32_t **width, uint32_t *count, uint32_t *n) {
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
    uint64_t stripes = 0, cosets = 0;
    bool valid = *first != NULL && *coset != NULL && *width != NULL;
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
    void *greedy, *collect, *expand, *augment, *check;
    kh_dptr_t allocations[32];
    unsigned allocated;
    const char *error;
} gpu_t;

static kh_dptr_t gpu_alloc(gpu_t *gpu, size_t bytes) {
    kh_dptr_t pointer = 0;
    if (gpu->error != NULL) {
        return 0;
    }
    if (gpu->allocated == 32 || gpu->cuda.cuMemAlloc(&pointer, bytes == 0 ? 4 : bytes) != 0) {
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

int main(int argc, char **argv) {
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }
    if (argc < 5) {
        help();
        return 1;
    }
    uint64_t p, r, threads = 1, start = 1, maximum = UINT64_C(2147483648), device = 0;
    uint64_t device_limit = 0, salt = 0x9E3779B9u;
    const char *poly_text = NULL;
    const char *test_cells = NULL;
    const char *error = NULL;
    kh_parameters_t parameters;

    if (!kh_parse_u64(argv[1], &p) || !kh_parse_u64(argv[2], &r) || p > UINT32_MAX || r > UINT32_MAX ||
        !kh_parameters((uint32_t)p, (uint32_t)r, &parameters, &error)) {
        fprintf(stderr, "kh_gpu_match_kernel: invalid dimensions\n");
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
            fprintf(stderr, "kh_gpu_match_kernel: invalid value for %s\n", option);
            return 1;
        }
        if (!strcmp(option, "--threads")) threads = value;
        else if (!strcmp(option, "--start")) start = value;
        else if (!strcmp(option, "--max-bytes")) maximum = value;
        else if (!strcmp(option, "--device")) device = value;
        else if (!strcmp(option, "--max-device-bytes")) device_limit = value;
        else if (!strcmp(option, "--salt")) salt = value;
        else {
            fprintf(stderr, "kh_gpu_match_kernel: unsupported option %s\n", option);
            return 1;
        }
    }
    if (threads == 0 || threads > 1024 || start > UINT32_MAX || maximum == 0 || device > 64 ||
        salt > UINT32_MAX || parameters.f > 65535) {
        fprintf(stderr, "kh_gpu_match_kernel: invalid resource controls or F above 65535\n");
        return 1;
    }
    uint64_t *bfirst = NULL;
    uint32_t *bcoset = NULL, *bwidth = NULL, nblocks = 0, n = 0;
    if (!blocks_read(argv[3], &parameters, &bfirst, &bcoset, &bwidth, &nblocks, &n)) {
        fprintf(stderr, "kh_gpu_match_kernel: invalid request blocks\n");
        return 1;
    }
    uint64_t q = parameters.q, f = parameters.f;
    // cells+right per label; left, choice, root, parent, viak, two queues per request;
    // per-root claim state (bounded by the greedy remainder, budgeted at n/8) and counters.
    uint64_t device_required = 8 * q + 24 * (uint64_t)n + 18 * ((uint64_t)n / 8 + 1) +
                               16 * (uint64_t)nblocks + UINT64_C(16777216);
    uint64_t host_required = 4 * q + 4 * (uint64_t)parameters.budget * threads +
                             UINT64_C(8388608) * threads + 4 * (uint64_t)n + UINT64_C(67108864);
    if (host_required > maximum) {
        fprintf(stderr, "kh_gpu_match_kernel: requires at least %" PRIu64 " host bytes; limit=%" PRIu64 "\n",
                host_required, maximum);
        return 1;
    }

    double t0 = now();
    gpu_t gpu = {0};
    if (!kh_cuda_open(&gpu.cuda, (int)device, kh_match_images, kh_match_images_count, &error)) {
        fprintf(stderr, "kh_gpu_match_kernel: %s\n", error);
        return 1;
    }
    size_t free_bytes = 0, total_bytes = 0;
    gpu.cuda.cuMemGetInfo(&free_bytes, &total_bytes);
    if (device_required > free_bytes || (device_limit != 0 && device_required > device_limit)) {
        fprintf(stderr, "kh_gpu_match_kernel: requires %" PRIu64 " device bytes; %zu free on %s\n",
                device_required, free_bytes, gpu.cuda.name);
        kh_cuda_close(&gpu.cuda);
        return 1;
    }
    gpu.greedy = kh_cuda_function(&gpu.cuda, "greedy");
    gpu.collect = kh_cuda_function(&gpu.cuda, "collect_roots");
    gpu.expand = kh_cuda_function(&gpu.cuda, "expand");
    gpu.augment = kh_cuda_function(&gpu.cuda, "augment");
    gpu.check = kh_cuda_function(&gpu.cuda, "check");
    if (!gpu.greedy || !gpu.collect || !gpu.expand || !gpu.augment || !gpu.check) {
        fprintf(stderr, "kh_gpu_match_kernel: embedded module lacks kernels\n");
        kh_cuda_close(&gpu.cuda);
        return 1;
    }

    uint16_t polynomial[32] = {0};
    uint32_t candidate = 0;
    if (poly_text != NULL) {
        if (!polynomial_parse(poly_text, &parameters, polynomial) || !kh_primitive(&parameters, polynomial)) {
            fprintf(stderr, "kh_gpu_match_kernel: polynomial is not primitive with generator X\n");
            return 1;
        }
    } else if (!kh_generate_polynomial(&parameters, (uint32_t)start, polynomial, &candidate)) {
        fprintf(stderr, "kh_gpu_match_kernel: primitive polynomial candidates exhausted\n");
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
            fprintf(stderr, "kh_gpu_match_kernel: invalid --test-cells table\n");
            return 1;
        }
    } else if (!kh_build_field(&parameters, polynomial, (uint32_t)threads, maximum, &field, &error)) {
        fprintf(stderr, "kh_gpu_match_kernel: %s\n", error);
        return 1;
    }
    double t_field = now();

    device_graph_t graph = {0};
    graph.cells = gpu_alloc(&gpu, q * 4);
    graph.bfirst = gpu_alloc(&gpu, nblocks * 8);
    graph.bcoset = gpu_alloc(&gpu, nblocks * 4);
    graph.bwidth = gpu_alloc(&gpu, nblocks * 4);
    graph.nblocks = nblocks;
    graph.f = (uint32_t)f;
    graph.qm1 = (uint32_t)(q - 1);
    graph.n = n;
    kh_dptr_t left = gpu_alloc(&gpu, (uint64_t)n * 4);
    kh_dptr_t choice = gpu_alloc(&gpu, (uint64_t)n * 2 + 4);
    kh_dptr_t right = gpu_alloc(&gpu, q * 4);
    kh_dptr_t root = gpu_alloc(&gpu, (uint64_t)n * 4);
    kh_dptr_t parent = gpu_alloc(&gpu, (uint64_t)n * 4);
    kh_dptr_t viak = gpu_alloc(&gpu, (uint64_t)n * 2 + 4);
    kh_dptr_t queue_a = gpu_alloc(&gpu, (uint64_t)n * 4);
    kh_dptr_t queue_b = gpu_alloc(&gpu, (uint64_t)n * 4);
    kh_dptr_t counters = gpu_alloc(&gpu, 64);  // u32: [0] matched [1] found [2] tail [3] longest [4] bad [5] roots [6] checked; u64 scans at byte 32
    kh_dptr_t scans = counters + 32;
    if (gpu.error == NULL) {
        gpu_ok(&gpu, gpu.cuda.cuMemcpyHtoD(graph.cells, field.cells, q * 4), "upload cells");
        gpu_ok(&gpu, gpu.cuda.cuMemcpyHtoD(graph.bfirst, bfirst, nblocks * 8), "upload blocks");
        gpu_ok(&gpu, gpu.cuda.cuMemcpyHtoD(graph.bcoset, bcoset, nblocks * 4), "upload blocks");
        gpu_ok(&gpu, gpu.cuda.cuMemcpyHtoD(graph.bwidth, bwidth, nblocks * 4), "upload blocks");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(left, FREE, n), "init");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(right, FREE, q), "init");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD8(choice, 0, (uint64_t)n * 2), "init");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD8(counters, 0, 64), "init");
    }
    if (test_cells != NULL) {
        free(field.cells);
        field.cells = NULL;
    }
    kh_free_field(&field);
    double t_upload = now();

    uint32_t salt32 = (uint32_t)salt;
    void *greedy_args[] = {&graph, &left, &choice, &right, &counters, &scans, &salt32};
    unsigned warp_blocks = (unsigned)(((uint64_t)n * 32 + 255) / 256);
    if (gpu.error == NULL) {
        gpu_ok(&gpu, kh_cuda_launch(&gpu.cuda, gpu.greedy, warp_blocks, 256, greedy_args), "greedy");
        gpu_ok(&gpu, gpu.cuda.cuCtxSynchronize(), "greedy");
    }
    uint32_t matched = gpu.error == NULL ? read_u32(&gpu, counters) : 0;
    double t_greedy = now();
    fprintf(stderr, "greedy matched=%u/%u seconds=%.3f\n", matched, n, t_greedy - t_upload);
    progress(matched, n, "greedy");

    uint64_t phases = 0;
    bool exhausted = false;
    unsigned base = gpu.allocated;
    while (gpu.error == NULL && matched < n) {
        double tp = now();
        uint32_t nroots = n - matched;
        gpu_free_last(&gpu, base);
        kh_dptr_t roots = gpu_alloc(&gpu, (uint64_t)nroots * 4);
        kh_dptr_t rootdone = gpu_alloc(&gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_u = gpu_alloc(&gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_v = gpu_alloc(&gpu, (uint64_t)nroots * 4);
        kh_dptr_t end_k = gpu_alloc(&gpu, (uint64_t)nroots * 2 + 4);
        if (gpu.error != NULL) {
            break;
        }
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(rootdone, 0, nroots), "phase init");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(counters + 4, 0, 5), "phase init");
        kh_dptr_t root_count = counters + 20;
        void *collect_args[] = {&n, &left, &root, &roots, &root_count};
        gpu_ok(&gpu, kh_cuda_launch(&gpu.cuda, gpu.collect, (n + 255) / 256, 256, collect_args), "collect");
        if (gpu.error == NULL && read_u32(&gpu, root_count) != nroots) {
            gpu.error = "free request count disagrees with matched count";
            break;
        }
        kh_dptr_t front = roots, next = queue_a, spare = queue_b;
        uint32_t count = nroots, levels = 0;
        uint64_t visited = nroots;
        kh_dptr_t tail = counters + 8, found = counters + 4, longest = counters + 12;
        while (gpu.error == NULL && count != 0) {
            gpu_ok(&gpu, gpu.cuda.cuMemsetD32(tail, 0, 1), "level");
            void *expand_args[] = {&graph, &count, &front, &next, &tail, &right, &root, &parent, &viak,
                                   &rootdone, &end_u, &end_v, &end_k, &found, &scans};
            unsigned blocks = (unsigned)(((uint64_t)count * 32 + 255) / 256);
            gpu_ok(&gpu, kh_cuda_launch(&gpu.cuda, gpu.expand, blocks, 256, expand_args), "expand");
            count = read_u32(&gpu, tail);
            visited += count;
            ++levels;
            front = next;
            next = next == queue_a ? queue_b : queue_a;
            (void)spare;
        }
        uint32_t augmented = read_u32(&gpu, found);
        if (augmented != 0) {
            void *augment_args[] = {&nroots, &roots, &left, &choice, &right, &parent, &viak, &rootdone,
                                    &end_u, &end_v, &end_k, &counters, &longest};
            gpu_ok(&gpu, kh_cuda_launch(&gpu.cuda, gpu.augment, (nroots + 255) / 256, 256, augment_args), "augment");
        }
        uint32_t after = read_u32(&gpu, counters);
        if (gpu.error == NULL && after - matched != augmented) {
            gpu.error = "augmentation count disagrees with claimed paths";
        }
        ++phases;
        fprintf(stderr, "phase=%" PRIu64 " augmented=%u matched=%u/%u levels=%u longest=%u visited=%" PRIu64
                " seconds=%.3f\n", phases, augmented, after, n, levels, read_u32(&gpu, longest), visited, now() - tp);
        matched = after;
        progress(matched, n, "augmenting");
        if (augmented == 0) {
            exhausted = true;  // root[] now marks the alternating closure: the Hall set.
            break;
        }
    }
    double t_solve = now();

    // Independent device-side check of every selected edge and two-way ownership.
    if (gpu.error == NULL) {
        kh_dptr_t bad = counters + 16, checked = counters + 24;
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(bad, 0, 1), "check");
        gpu_ok(&gpu, gpu.cuda.cuMemsetD32(checked, 0, 1), "check");
        void *check_args[] = {&graph, &left, &choice, &right, &bad, &checked};
        gpu_ok(&gpu, kh_cuda_launch(&gpu.cuda, gpu.check, (n + 255) / 256, 256, check_args), "check");
        if (gpu.error == NULL && (read_u32(&gpu, bad) != 0 || read_u32(&gpu, checked) != matched)) {
            gpu.error = "device self-check failed";
        }
    }
    bool obstructed = matched < n;
    if (gpu.error == NULL && obstructed && !exhausted) {
        gpu.error = "search ended without a completed exhaustive phase";
    }
    uint64_t scan_count = 0;
    if (gpu.error == NULL) {
        gpu_ok(&gpu, gpu.cuda.cuMemcpyDtoH(&scan_count, scans, 8), "scans");
    }

    uint16_t *choices = malloc((size_t)n * 2 + 4);
    uint32_t *lefts = obstructed ? malloc((size_t)n * 4) : NULL;
    uint32_t *roots_host = obstructed ? malloc((size_t)n * 4) : NULL;
    uint32_t hall_left = 0, hall_right = 0;
    if (gpu.error == NULL && (choices == NULL || (obstructed && (lefts == NULL || roots_host == NULL)))) {
        gpu.error = "host allocation failed";
    }
    if (gpu.error == NULL) {
        gpu_ok(&gpu, gpu.cuda.cuMemcpyDtoH(choices, choice, (uint64_t)n * 2), "download");
        if (obstructed) {
            gpu_ok(&gpu, gpu.cuda.cuMemcpyDtoH(lefts, left, (uint64_t)n * 4), "download");
            gpu_ok(&gpu, gpu.cuda.cuMemcpyDtoH(roots_host, root, (uint64_t)n * 4), "download");
        }
    }
    gpu_free_last(&gpu, 0);
    char device_name[128];
    snprintf(device_name, sizeof device_name, "%s", gpu.cuda.name);
    if (gpu.error != NULL) {
        fprintf(stderr, "kh_gpu_match_kernel: %s\n", gpu.error);
        kh_cuda_close(&gpu.cuda);
        return 1;
    }
    kh_cuda_close(&gpu.cuda);

    int descriptor = open(argv[4], O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (descriptor < 0) {
        perror("kh_gpu_match_kernel: output");
        return 1;
    }
    FILE *file = fdopen(descriptor, "wb");
    packer_t packer = {file, 0, 0, file != NULL};
    uint32_t bits = 0;
    for (uint32_t value = (uint32_t)f - 1 + obstructed; value != 0; value >>= 1) {
        ++bits;
    }
    for (uint32_t u = 0; packer.ok && u < n; ++u) {
        uint32_t value = obstructed ? (lefts[u] == FREE ? 0 : choices[u] + 1u) : choices[u];
        pack(&packer, value, bits);
    }
    pack_flush(&packer);
    if (obstructed) {
        for (uint32_t u = 0; packer.ok && u < n; ++u) {
            uint32_t member = roots_host[u] != FREE;
            hall_left += member;
            pack(&packer, member, 1);
        }
        pack_flush(&packer);
        hall_right = hall_left - (n - matched);
    }
    bool success = packer.ok && file != NULL && fflush(file) == 0 && fsync(fileno(file)) == 0;
    if (file == NULL || fclose(file) != 0) {
        success = false;
    }
    free(choices);
    free(lefts);
    free(roots_host);
    if (!success) {
        unlink(argv[4]);
        fprintf(stderr, "kh_gpu_match_kernel: payload write failed\n");
        return 1;
    }
    double t_end = now();
    printf("{\"p\":%u,\"r\":%u,\"candidate\":%u,\"polynomial\":[", parameters.p, parameters.r, candidate);
    for (uint32_t index = 0; index <= parameters.r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    printf("],\"status\":%u,\"required\":%u,\"matched\":%u,\"phases\":%" PRIu64 ",\"scans\":%" PRIu64
           ",\"memory_required\":%" PRIu64 ",\"hall_left\":%u,\"hall_right\":%u,\"engine\":\"gpu\","
           "\"device\":\"%s\",\"device_bytes\":%" PRIu64 ",\"seconds\":{\"setup\":%.3f,\"field\":%.3f,"
           "\"upload\":%.3f,\"greedy\":%.3f,\"augment\":%.3f,\"output\":%.3f}}\n",
           obstructed, n, matched, phases, scan_count, host_required, hall_left, hall_right, device_name,
           device_required, 0.0, t_field - t0, t_upload - t_field, t_greedy - t_upload,
           t_solve - t_greedy, t_end - t_solve);
    fflush(stdout);
    free(bfirst);
    free(bcoset);
    free(bwidth);
    kh_resource_print("solver", 0);
    return obstructed ? 2 : 0;
}
