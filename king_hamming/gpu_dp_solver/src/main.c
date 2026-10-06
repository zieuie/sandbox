#define _GNU_SOURCE

// GPU drop-in for kh_dp_tile: same arguments, admission, and byte-identical outputs.

#include "kh_cuda.h"
#include "kh_solver.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

extern const kh_cuda_image_t kh_dp_images[];
extern const unsigned kh_dp_images_count;

// A transition as the host prepares it; the device gets hot_t and bound_t (kernels.cu).
typedef struct {
    uint32_t du;
    uint32_t dv;
    uint32_t id;
    uint32_t gain;
} hot_transition_t;

// Mirrors hot_t in kernels.cu.
typedef struct {
    uint64_t offset;   // du * width + dv
    uint32_t id;
    uint32_t gain;
} device_transition_t;

// Mirrors bound_t in kernels.cu.
typedef struct {
    uint32_t du;
    uint32_t dv;
} device_bound_t;

// On Pascal, tables smaller than this stay in the original order (13^9's 1,838 and 23^7's 9,969
// transitions measured no faster sorted; 31^7's 24,179 measured 1.34x faster sorted).
#define ORDERED_TABLE_LIMIT 12000

static int compare_offsets(const void *left, const void *right) {
    const hot_transition_t *a = left, *b = right;
    if (a->du != b->du) return a->du < b->du ? -1 : 1;
    if (a->dv != b->dv) return a->dv < b->dv ? -1 : 1;
    return (a->id > b->id) - (a->id < b->id);
}

typedef struct {
    kh_parameters_t parameters;
    uint64_t *values;
    uint32_t *choices;
    uint32_t first_u, last_u, first_v, last_v, origin_u, origin_v;
    size_t width, tile_width;
} tile_t;

static void help(void) {
    puts("Compute one exact DP tile on a GPU; drop-in for kh_dp_tile with identical output bytes.\n"
         "Usage: ./kh_gpu_dp_tile P R FIRST_U LAST_U FIRST_V LAST_V INPUT OUTPUT_DIR [THREADS] [MAX_BYTES]\n"
         "Example: ./kh_gpu_dp_tile 5 3 1 7 1 7 halo.bin tile_0_0 2 2147483648\n"
         "Input is row-major values for [max(0,FIRST_U-p*p),LAST_U] x\n"
         "[max(0,FIRST_V-p*p),LAST_V], including ignored tile interior.\n"
         "Output directory contains values.bin, choices.bin, and tile.json.\n"
         "THREADS only enters tile.json's admitted payload, as in kh_dp_tile.\n"
         "Environment: KH_GPU_DEVICE selects the CUDA device (default 0).\n"
         "Exit 3 means no usable GPU (callers may fall back to kh_dp_tile); 1 is any other error.");
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static bool finish(FILE *file) {
    bool ok = !ferror(file) && fflush(file) == 0 && fsync(fileno(file)) == 0;
    if (fclose(file) != 0) {
        ok = false;
    }
    return ok;
}

// Identical to kh_dp_tile's save(): interior values, choices, and metadata, all synced.
static bool save(const tile_t *tile, const char *directory, uint64_t payload) {
    char path[8192];
    snprintf(path, sizeof path, "%s/values.bin", directory);
    FILE *values = fopen(path, "wb");
    if (values == NULL) {
        return false;
    }
    for (uint32_t u = tile->first_u; u <= tile->last_u; ++u) {
        size_t offset = (size_t)(u - tile->origin_u) * tile->width + tile->first_v - tile->origin_v;
        if (fwrite(tile->values + offset, sizeof(uint64_t), tile->tile_width, values) != tile->tile_width) {
            fclose(values);
            return false;
        }
    }
    if (!finish(values)) {
        return false;
    }
    snprintf(path, sizeof path, "%s/choices.bin", directory);
    FILE *choices = fopen(path, "wb");
    if (choices == NULL) {
        return false;
    }
    size_t cells = (size_t)(tile->last_u - tile->first_u + 1) * tile->tile_width;
    bool wrote = fwrite(tile->choices, sizeof(uint32_t), cells, choices) == cells;
    if (!finish(choices) || !wrote) {
        return false;
    }
    snprintf(path, sizeof path, "%s/tile.json", directory);
    FILE *metadata = fopen(path, "w");
    if (metadata == NULL) {
        return false;
    }
    uint16_t endian = 1;
    const char *byteorder = *(unsigned char *)&endian ? "little" : "big";
    fprintf(metadata,
            "{\"format\":\"KH-DP-TILE-1\",\"p\":%u,\"r\":%u,\"first_u\":%u,\"last_u\":%u,"
            "\"first_v\":%u,\"last_v\":%u,\"byteorder\":\"%s\",\"value_bytes\":8,\"choice_bytes\":4,"
            "\"memory_payload_bytes\":%" PRIu64 "}\n",
            tile->parameters.p, tile->parameters.r, tile->first_u, tile->last_u,
            tile->first_v, tile->last_v, byteorder, payload);
    if (!finish(metadata)) {
        return false;
    }
    int descriptor = open(directory, O_RDONLY | O_DIRECTORY);
    if (descriptor < 0) {
        return false;
    }
    bool durable = fsync(descriptor) == 0;
    close(descriptor);
    return durable;
}

static bool multiply(uint64_t a, uint64_t b, uint64_t *output) {
    if (b != 0 && a > UINT64_MAX / b) {
        return false;
    }
    *output = a * b;
    return true;
}

static void progress(uint64_t done, uint64_t total, const char *phase) {
    printf("{\"done\":%" PRIu64 ",\"total\":%" PRIu64 ",\"checkpoint_done\":0,\"checkpoint_tiles\":0,"
           "\"threads\":1,\"units\":\"cells\",\"phase\":\"%s\",\"message\":\"dp cells (gpu)\",\"heartbeat\":true,"
           "\"engine\":\"gpu\"}\n",
           done, total, phase);
    fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }
    if (argc < 9 || argc > 11) {
        help();
        return 1;
    }
    uint64_t arguments[8] = {0};
    for (int index = 0; index < 6; ++index) {
        if (!kh_parse_u64(argv[index + 1], &arguments[index]) || arguments[index] > UINT32_MAX) {
            fprintf(stderr, "kh_gpu_dp_tile: invalid coordinate\n");
            return 1;
        }
    }
    arguments[6] = 1;
    arguments[7] = UINT64_C(2147483648);
    for (int index = 9; index < argc; ++index) {
        if (!kh_parse_u64(argv[index], &arguments[index - 3]) || arguments[index - 3] == 0) {
            return 1;
        }
    }
    tile_t tile = {0};
    const char *error = NULL;
    if (!kh_parameters_dp64((uint32_t)arguments[0], (uint32_t)arguments[1], &tile.parameters, &error)) {
        fprintf(stderr, "kh_gpu_dp_tile: %s\n", error);
        return 1;
    }
    tile.first_u = (uint32_t)arguments[2];
    tile.last_u = (uint32_t)arguments[3];
    tile.first_v = (uint32_t)arguments[4];
    tile.last_v = (uint32_t)arguments[5];
    if (tile.first_u == 0 || tile.first_v == 0 || tile.last_u < tile.first_u || tile.last_v < tile.first_v ||
        tile.last_u > tile.parameters.budget || tile.last_v > tile.parameters.budget ||
        arguments[6] > UINT32_MAX || strlen(argv[8]) > 3800) {
        fprintf(stderr, "kh_gpu_dp_tile: invalid tile bounds or operational limits\n");
        return 1;
    }
    uint32_t radius = (uint32_t)tile.parameters.p * tile.parameters.p;
    tile.origin_u = tile.first_u > radius ? tile.first_u - radius : 0;
    tile.origin_v = tile.first_v > radius ? tile.first_v - radius : 0;
    tile.width = (size_t)tile.last_v - tile.origin_v + 1;
    tile.tile_width = (size_t)tile.last_v - tile.first_v + 1;
    uint64_t value_bytes, choice_bytes, cells, transition_bytes, stack_bytes;

    // Same admission arithmetic as kh_dp_tile so tile.json's payload is byte-identical.
    if (!multiply((uint64_t)tile.last_u - tile.origin_u + 1, tile.width, &cells) ||
        !multiply(cells, 8, &value_bytes) ||
        !multiply((uint64_t)tile.last_u - tile.first_u + 1, tile.tile_width, &cells) ||
        !multiply(cells, 4, &choice_bytes) ||
        !multiply((uint64_t)tile.parameters.p * tile.parameters.p, (uint64_t)tile.parameters.p * 24, &transition_bytes) ||
        !multiply(arguments[6], UINT64_C(8388608), &stack_bytes) ||
        value_bytes > SIZE_MAX || choice_bytes > SIZE_MAX ||
        value_bytes > arguments[7] || choice_bytes > arguments[7] - value_bytes ||
        transition_bytes > arguments[7] - value_bytes - choice_bytes ||
        stack_bytes > arguments[7] - value_bytes - choice_bytes - transition_bytes) {
        fprintf(stderr, "kh_gpu_dp_tile: tile and halo exceed memory admission\n");
        return 1;
    }
    uint64_t payload = value_bytes + choice_bytes + transition_bytes + stack_bytes;
    if (arguments[7] - payload < UINT64_C(67108864)) {
        fprintf(stderr, "kh_gpu_dp_tile: insufficient memory reserve\n");
        return 1;
    }
    payload += UINT64_C(67108864);
    // No RLIMIT_AS here: a CUDA context reserves far more virtual address space than it
    // uses. Host memory stays bounded by the same checked payload as kh_dp_tile.

    struct stat status;
    if (stat(argv[7], &status) != 0 || status.st_size < 0 || (uint64_t)status.st_size != value_bytes ||
        lstat(argv[8], &status) == 0 || errno != ENOENT) {
        fprintf(stderr, "kh_gpu_dp_tile: incorrect input size or existing output\n");
        return 1;
    }

    double t0 = now();
    kh_cuda_t cuda;
    const char *device_text = getenv("KH_GPU_DEVICE");
    int device = device_text != NULL ? atoi(device_text) : 0;
    if (!kh_cuda_open(&cuda, device, kh_dp_images, kh_dp_images_count, &error)) {
        fprintf(stderr, "kh_gpu_dp_tile: %s\n", error);
        kh_cuda_close(&cuda);
        return 3;
    }
    void *row_kernel = kh_cuda_function(&cuda, "dp_row");
    size_t free_bytes = 0, total_bytes = 0;
    cuda.cuMemGetInfo(&free_bytes, &total_bytes);
    if (row_kernel == NULL || value_bytes + choice_bytes + transition_bytes + UINT64_C(33554432) > free_bytes) {
        fprintf(stderr, "kh_gpu_dp_tile: GPU kernel missing or insufficient device memory (%zu free)\n", free_bytes);
        kh_cuda_close(&cuda);
        return 3;
    }

    FILE *input = fopen(argv[7], "rb");
    tile.values = malloc((size_t)value_bytes);
    tile.choices = malloc((size_t)choice_bytes);
    if (input == NULL || tile.values == NULL || tile.choices == NULL) {
        fprintf(stderr, "kh_gpu_dp_tile: cannot allocate or read tile state\n");
        return 1;
    }
    bool read_ok = fread(tile.values, 1, (size_t)value_bytes, input) == value_bytes;
    fclose(input);
    kh_transition_table_t transitions = {0};
    if (!read_ok || !kh_build_transitions(&tile.parameters, true, &transitions, &error)) {
        fprintf(stderr, "kh_gpu_dp_tile: input or transition construction failed\n");
        return 1;
    }
    hot_transition_t *hot = malloc((size_t)transitions.count * sizeof *hot + 16);
    if (hot == NULL) {
        fprintf(stderr, "kh_gpu_dp_tile: cannot allocate precomputed transitions\n");
        return 1;
    }
    for (uint32_t index = 0; index < transitions.count; ++index) {
        kh_transition_t *transition = &transitions.entries[index];
        hot[index] = (hot_transition_t){
            (uint32_t)transition->a * transition->t,
            (uint32_t)transition->b * transition->t,
            kh_transition_id(tile.parameters.p, transition),
            transition->gain,
        };
    }
    // Scan order for locality (see kernels.cu): by predecessor row, then column. The kernel
    // breaks ties by original id, so the result is the same as in the original order.
    // Sorted by offset except small tables on Pascal (the P600s), where the original order with
    // strict '>' measured faster (docs/DP_KERNEL_PROFILE.md). KH_DP_ORDER=sorted|original overrides.
    const char *order_text = getenv("KH_DP_ORDER");
    uint32_t ordered = order_text != NULL ? strcmp(order_text, "original") == 0
                                          : cuda.arch < 70 && transitions.count < ORDERED_TABLE_LIMIT;
    if (!ordered) {
        qsort(hot, transitions.count, sizeof *hot, compare_offsets);
    }
    // The device reads each predecessor at a fixed offset before the cell; near the DP's zero
    // edges (a predecessor row or column below 1) it also needs du and dv to skip it.
    device_transition_t *device_hot = malloc((size_t)transitions.count * sizeof *device_hot + 16);
    device_bound_t *device_bounds = malloc((size_t)transitions.count * sizeof *device_bounds + 16);
    if (device_hot == NULL || device_bounds == NULL) {
        fprintf(stderr, "kh_gpu_dp_tile: cannot allocate precomputed transitions\n");
        return 1;
    }
    uint32_t max_du = 0, max_dv = 0;
    for (uint32_t index = 0; index < transitions.count; ++index) {
        device_hot[index] = (device_transition_t){(uint64_t)hot[index].du * tile.width + hot[index].dv,
                                                   hot[index].id, hot[index].gain};
        device_bounds[index] = (device_bound_t){hot[index].du, hot[index].dv};
        if (hot[index].du > max_du) max_du = hot[index].du;
        if (hot[index].dv > max_dv) max_dv = hot[index].dv;
    }
    uint32_t checked = tile.first_u <= max_du || tile.first_v <= max_dv;

    kh_dptr_t d_values = 0, d_choices = 0, d_hot = 0, d_bounds = 0;
    int status_code = 0;
    const char *stage = "allocate";
    if ((status_code = cuda.cuMemAlloc(&d_values, value_bytes)) == 0 &&
        (status_code = cuda.cuMemAlloc(&d_choices, choice_bytes)) == 0 &&
        (status_code = cuda.cuMemAlloc(&d_hot, (size_t)transitions.count * sizeof *device_hot + 16)) == 0 &&
        (status_code = cuda.cuMemAlloc(&d_bounds, (size_t)transitions.count * sizeof *device_bounds + 16)) == 0) {
        stage = "upload";
        if ((status_code = cuda.cuMemcpyHtoD(d_values, tile.values, value_bytes)) == 0 &&
            (status_code = cuda.cuMemsetD8(d_choices, 0, choice_bytes)) == 0 &&
            (status_code = cuda.cuMemcpyHtoD(d_hot, device_hot, (size_t)transitions.count * sizeof *device_hot)) == 0 &&
            (status_code = cuda.cuMemcpyHtoD(d_bounds, device_bounds,
                                             (size_t)transitions.count * sizeof *device_bounds)) == 0) {
            stage = "compute";
            // Warps per block, each scanning a slice of the table. 16 measured best for large tables
            // on the RTX 3060 and the P600 (2026-10-06, docs/DP_KERNEL_PROFILE.md); KH_DP_SLICES
            // (1-32) overrides it for experiments.
            uint32_t slices = transitions.count >= 256 ? 16 : 8;
            const char *slices_text = getenv("KH_DP_SLICES");
            if (slices_text != NULL && atoi(slices_text) >= 1 && atoi(slices_text) <= 32) slices = (uint32_t)atoi(slices_text);
            unsigned blocks = (unsigned)((tile.tile_width + 31) / 32);
            uint64_t width = tile.width, tile_width = tile.tile_width, total = choice_bytes / 4;
            uint32_t count = transitions.count;
            double last_report = now();
            progress(0, total, "computing");
            for (uint32_t u = tile.first_u; status_code == 0 && u <= tile.last_u; ++u) {
                void *args[] = {&d_values, &d_choices, &d_hot, &d_bounds, &count, &checked, &ordered, &u, &tile.first_u,
                                &tile.first_v, &tile.last_v, &tile.origin_u, &tile.origin_v, &width, &tile_width};
                status_code = kh_cuda_launch(&cuda, row_kernel, blocks, slices * 32, args);
                if (status_code == 0 && now() - last_report > 1.0) {
                    status_code = cuda.cuCtxSynchronize();
                    progress((uint64_t)(u - tile.first_u + 1) * tile.tile_width, total, "computing");
                    last_report = now();
                }
            }
            if (status_code == 0) {
                status_code = cuda.cuCtxSynchronize();
            }
            if (status_code == 0) {
                stage = "download";
                status_code = cuda.cuMemcpyDtoH(tile.values, d_values, value_bytes);
            }
            if (status_code == 0) {
                status_code = cuda.cuMemcpyDtoH(tile.choices, d_choices, choice_bytes);
            }
            if (status_code == 0) {
                progress(total, total, "computing");
            }
        }
    }
    if (status_code != 0) {
        fprintf(stderr, "kh_gpu_dp_tile: CUDA %s failed: %s\n", stage, kh_cuda_error(&cuda, status_code));
        kh_cuda_close(&cuda);
        return 3;
    }
    char device_name[128];
    snprintf(device_name, sizeof device_name, "%s", cuda.name);
    cuda.cuMemFree(d_values);
    cuda.cuMemFree(d_choices);
    cuda.cuMemFree(d_hot);
    cuda.cuMemFree(d_bounds);
    free(device_hot);
    free(device_bounds);
    kh_cuda_close(&cuda);
    double t_compute = now();

    char staging[4096];
    snprintf(staging, sizeof staging, "%s.tmp-XXXXXX", argv[8]);
    bool complete = mkdtemp(staging) != NULL && save(&tile, staging, payload);
    if (complete) {
        complete = syscall(SYS_renameat2, AT_FDCWD, staging, AT_FDCWD, argv[8], RENAME_NOREPLACE) == 0;
    }
    if (complete) {
        char parent[4096];
        strcpy(parent, argv[8]);
        char *separator = strrchr(parent, '/');
        if (separator == NULL) {
            strcpy(parent, ".");
        } else if (separator == parent) {
            separator[1] = '\0';
        } else {
            *separator = '\0';
        }
        int descriptor = open(parent, O_RDONLY | O_DIRECTORY);
        complete = descriptor >= 0 && fsync(descriptor) == 0;
        if (descriptor >= 0) {
            close(descriptor);
        }
    }
    free(transitions.entries);
    free(hot);
    free(tile.values);
    free(tile.choices);
    if (!complete) {
        fprintf(stderr, "kh_gpu_dp_tile: durable publication failed; private staging may remain\n");
        return 1;
    }
    fprintf(stderr, "kh_gpu_dp_tile: device=%s transitions=%u compute_seconds=%.3f\n",
            device_name, transitions.count, t_compute - t0);
    return 0;
}
