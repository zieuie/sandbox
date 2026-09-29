#define _GNU_SOURCE

#include "kh_solver.h"
#include "kh_threads.h"
#include "kh_progress.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

// One bounded tile plus the predecessor rectangle shared by all local threads.
typedef struct {
    kh_parameters_t parameters;
    kh_transition_table_t transitions;
    uint64_t *values;
    uint32_t *choices;
    uint32_t first_u;
    uint32_t last_u;
    uint32_t first_v;
    uint32_t last_v;
    uint32_t origin_u;
    uint32_t origin_v;
    size_t width;
    size_t tile_width;
} tile_context_t;

/*
 * Explain the bounded tile interface.
 * Parameters: none.
 * Returns: No value; prints help and a runnable interface example.
 */
static void help(void) {
    puts("Compute one exact DP tile from a native uint64 predecessor rectangle.\n"
         "Usage: ./kh_dp_tile P R FIRST_U LAST_U FIRST_V LAST_V INPUT OUTPUT_DIR [THREADS] [MAX_BYTES]\n"
         "Example: ./kh_dp_tile 5 3 1 7 1 7 halo.bin tile_0_0 2 2147483648\n"
         "Input is row-major values for [max(0,FIRST_U-p*p),LAST_U] x\n"
         "[max(0,FIRST_V-p*p),LAST_V], including ignored tile interior.\n"
         "Output directory contains values.bin, choices.bin, and tile.json.\n"
         "Thread default: 1; memory default: 2 GiB. Output must not already exist.");
}

/*
 * Evaluate one cell using exact affordable transitions and stable original IDs.
 * Parameters: context: Shared tile_context_t; u,v: Cell coordinates owned by this invocation.
 * Returns: No value; writes one optimum and choice, preserving earliest strict-improvement ties.
 */
static void evaluate(void *context, uint32_t u, uint32_t v) {
    tile_context_t *tile = context;
    size_t cell = (size_t)(u - tile->origin_u) * tile->width + v - tile->origin_v;
    size_t choice = (size_t)(u - tile->first_u) * tile->tile_width + v - tile->first_v;
    tile->values[cell] = 0;
    tile->choices[choice] = 0;

    // Both predecessor coordinates strictly decrease, including at tile boundaries.
    for (uint32_t index = 0; index < tile->transitions.count; ++index) {
        kh_transition_t transition = tile->transitions.entries[index];
        uint32_t du = (uint32_t)transition.a * transition.t;
        uint32_t dv = (uint32_t)transition.b * transition.t;

        // Global affordability is distinct from position within the local rectangle.
        if (du > u || dv > v) {
            continue;
        }
        size_t predecessor = (size_t)(u - du - tile->origin_u) * tile->width + v - dv - tile->origin_v;
        uint64_t candidate = tile->values[predecessor] + transition.gain;

        // Reduced transitions retain original scan order and choice identifiers.
        if (candidate > tile->values[cell]) {
            tile->values[cell] = candidate;
            tile->choices[choice] = kh_transition_id(tile->parameters.p, &transition);
        }
    }
}

/*
 * Flush an output file and its descriptor before publishing the directory.
 * Parameters: file: Open owned stream.
 * Returns: True on durable close, false on any write/flush failure.
 */
static bool finish(FILE *file) {
    bool ok = !ferror(file) && fflush(file) == 0 && fsync(fileno(file)) == 0;

    // Close even after failure so staging cleanup never leaks a descriptor.
    if (fclose(file) != 0) {
        ok = false;
    }
    return ok;
}

/*
 * Save tile-only arrays and metadata into a private staging directory.
 * Parameters: tile: Completed state; directory: Existing private directory; payload: Admitted memory bytes.
 * Returns: True after all files and directory are durable; false on I/O failure.
 */
static bool save(tile_context_t *tile, const char *directory, uint64_t payload) {
    char path[8192];
    snprintf(path, sizeof path, "%s/values.bin", directory);
    FILE *values = fopen(path, "wb");

    // No output file may become visible through the final directory before completion.
    if (values == NULL) {
        return false;
    }

    // Stream only the computed interior, omitting the predecessor halo.
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

/*
 * Compute a checked product used for allocation or input-size admission.
 * Parameters: a,b: Nonnegative factors; output: Product destination.
 * Returns: False on uint64 overflow, true with initialized output otherwise.
 */
static bool multiply(uint64_t a, uint64_t b, uint64_t *output) {

    // Divide before multiplication to reject arithmetic overflow.
    if (b != 0 && a > UINT64_MAX / b) {
        return false;
    }
    *output = a * b;
    return true;
}

/*
 * Parse, admit, evaluate and atomically publish one bounded immutable tile.
 * Parameters: argc: Argument count; argv: Input strings described by help.
 * Returns: Zero for help/success; one for invalid inputs, failed computation or publication.
 */
int main(int argc, char **argv) {

    // Empty invocation must be useful without creating files.
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }

    if (argc < 9 || argc > 11) {
        help();
        return 1;
    }
    uint64_t arguments[8] = {0};

    // Parse the six mathematical coordinates before optional operational limits.
    for (int index = 0; index < 6; ++index) {
        if (!kh_parse_u64(argv[index + 1], &arguments[index]) || arguments[index] > UINT32_MAX) {
            fprintf(stderr, "kh_dp_tile: invalid coordinate\n");
            return 1;
        }
    }
    arguments[6] = 1;
    arguments[7] = UINT64_C(2147483648);

    // Optional thread and memory controls use the same checked parser.
    for (int index = 9; index < argc; ++index) {
        if (!kh_parse_u64(argv[index], &arguments[index - 3]) || arguments[index - 3] == 0) {
            return 1;
        }
    }
    tile_context_t tile = {0};
    const char *error = NULL;

    if (!kh_parameters((uint32_t)arguments[0], (uint32_t)arguments[1], &tile.parameters, &error)) {
        fprintf(stderr, "kh_dp_tile: %s\n", error);
        return 1;
    }
    tile.first_u = (uint32_t)arguments[2];
    tile.last_u = (uint32_t)arguments[3];
    tile.first_v = (uint32_t)arguments[4];
    tile.last_v = (uint32_t)arguments[5];

    // Bound coordinates, thread count and path lengths before allocating anything.
    if (tile.first_u == 0 || tile.first_v == 0 || tile.last_u < tile.first_u || tile.last_v < tile.first_v ||
        tile.last_u > tile.parameters.budget || tile.last_v > tile.parameters.budget ||
        arguments[6] > UINT32_MAX || strlen(argv[8]) > 3800) {
        fprintf(stderr, "kh_dp_tile: invalid tile bounds or operational limits\n");
        return 1;
    }
    uint32_t radius = (uint32_t)tile.parameters.p * tile.parameters.p;
    tile.origin_u = tile.first_u > radius ? tile.first_u - radius : 0;
    tile.origin_v = tile.first_v > radius ? tile.first_v - radius : 0;
    tile.width = (size_t)tile.last_v - tile.origin_v + 1;
    tile.tile_width = (size_t)tile.last_v - tile.first_v + 1;
    uint64_t value_bytes;
    uint64_t choice_bytes;
    uint64_t cells;
    uint64_t transition_bytes;
    uint64_t stack_bytes;

    // Account for the halo, tile choices, peak transition construction and thread stacks.
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
        fprintf(stderr, "kh_dp_tile: tile and halo exceed memory admission\n");
        return 1;
    }
    uint64_t payload = value_bytes + choice_bytes + transition_bytes + stack_bytes;

    // Leave fixed headroom for libraries, pool bookkeeping and transition-sort scratch.
    if (arguments[7] - payload < UINT64_C(67108864)) {
        fprintf(stderr, "kh_dp_tile: insufficient memory reserve\n");
        return 1;
    }
    payload += UINT64_C(67108864);
    struct rlimit limit;

    // Enforce an address-space ceiling in addition to checked payload estimates.
    if (getrlimit(RLIMIT_AS, &limit) != 0) {
        return 1;
    }

    if (limit.rlim_cur == RLIM_INFINITY || limit.rlim_cur > arguments[7]) {
        limit.rlim_cur = (rlim_t)arguments[7];
    }

    if (setrlimit(RLIMIT_AS, &limit) != 0) {
        fprintf(stderr, "kh_dp_tile: cannot enforce memory ceiling\n");
        return 1;
    }
    struct stat status;

    // Require exact native input length and a fresh immutable output name.
    if (stat(argv[7], &status) != 0 || status.st_size < 0 || (uint64_t)status.st_size != value_bytes ||
        lstat(argv[8], &status) == 0 || errno != ENOENT) {
        fprintf(stderr, "kh_dp_tile: incorrect input size or existing output\n");
        return 1;
    }
    FILE *input = fopen(argv[7], "rb");
    tile.values = malloc((size_t)value_bytes);
    tile.choices = calloc((size_t)(choice_bytes / 4), sizeof(uint32_t));

    if (input == NULL || tile.values == NULL || tile.choices == NULL) {
        fprintf(stderr, "kh_dp_tile: cannot allocate or read tile state\n");
        return 1;
    }
    bool read_ok = fread(tile.values, 1, (size_t)value_bytes, input) == value_bytes;
    fclose(input);

    if (!read_ok || !kh_build_transitions(&tile.parameters, true, &tile.transitions, &error)) {
        fprintf(stderr, "kh_dp_tile: input or transition construction failed\n");
        return 1;
    }
    kh_pool_t *pool = kh_pool_create((uint32_t)arguments[6], evaluate, &tile, &error);

    if (pool == NULL) {
        fprintf(stderr, "kh_dp_tile: %s\n", error);
        return 1;
    }
    kh_progress_t *progress = kh_progress_start(pool, 0, 0, choice_bytes / 4, (uint32_t)arguments[6], 1000, &error);

    if (progress == NULL) {
        kh_pool_destroy(pool);
        return 1;
    }
    kh_pool_fill(pool, tile.first_u, tile.last_u, tile.first_v, tile.last_v);
    kh_progress_stop(progress);
    kh_pool_destroy(pool);
    char staging[4096];
    snprintf(staging, sizeof staging, "%s.tmp-XXXXXX", argv[8]);
    bool complete = mkdtemp(staging) != NULL && save(&tile, staging, payload);

    // Linux no-replace publication prevents even an empty destination from being overwritten.
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
    free(tile.transitions.entries);
    free(tile.values);
    free(tile.choices);

    if (!complete) {
        fprintf(stderr, "kh_dp_tile: durable publication failed; private staging may remain\n");
        return 1;
    }
    return 0;
}
