#define _GNU_SOURCE

#include "kh_matching.h"
#include "kh_checkpoint.h"
#include "kh_resource.h"

#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>
#include <unistd.h>

/*
 * Print the native kernel's compact-input and payload interface.
 * Parameters: none.
 * Returns: No value; prints useful help and an example.
 */
static void help(void) {
    puts("Exact local matching kernel over one shared primitive-X field.\n"
         "Usage: kh_match_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr]\n"
         "       [--start N] [--threads N] [--max-bytes N]\n"
         "       [--checkpoint PATH] [--resume PATH] [--dp-hash HEX]\n"
         "       [--checkpoint-seconds N] [--stop-after-phases N] [--checkpoint-handshake]\n"
         "Example: printf '1\\n1 1\\n' > /tmp/blocks.txt\n"
         "         ./kh_match_kernel 3 3 /tmp/blocks.txt /tmp/choices.bin\n"
         "Blocks: count, then one stripe-count and consecutive-coset-count pair per line.\n"
         "Output: packed neighbor choices, then Hall bits if obstructed; JSON metadata on stdout.\n"
         "Defaults: automatic polynomial, start=1, one pinned matching/field thread, 2 GiB.\n"
         "Exit 0: full matching; 2: exact obstruction; 3: checkpointed pause; 1: error.\n"
         "Resume currently requires an explicit --poly and the same DP hash.\n"
         "For normal DP-file usage, run python3 match.py instead.");
}

/*
 * Parse a canonical comma-separated primitive-polynomial candidate.
 * Parameters: text: Input coefficients; parameters: Dimensions; output: r+1 writable coefficients.
 * Returns: True for valid coefficient syntax; primitivity is checked separately.
 */
static bool polynomial_parse(const char *text, const kh_parameters_t *parameters, uint16_t *output) {
    char *copy = strdup(text);

    // Allocation failure leaves no owned parsing state.
    if (copy == NULL) {
        return false;
    }
    char *cursor = copy;
    bool valid = true;

    // Require exactly r+1 bounded coefficients, including the monic leading term.
    for (uint32_t index = 0; index <= parameters->r; ++index) {
        char *comma = strchr(cursor, ',');

        // Isolate one coefficient without modifying argv.
        if (comma != NULL) {
            *comma = '\0';
        }
        uint64_t coefficient;

        // Missing or extra separators are errors, even when the numeric prefix is valid.
        if (!kh_parse_u64(cursor, &coefficient) || coefficient >= parameters->p ||
            ((index < parameters->r) != (comma != NULL))) {
            valid = false;
            break;
        }
        output[index] = (uint16_t)coefficient;

        // Advance only when another coefficient is required.
        if (comma != NULL) {
            cursor = comma + 1;
        }
    }
    free(copy);
    return valid;
}

/*
 * Read compact graph blocks with checked total stripe and coset budgets.
 * Parameters: path: Text input; parameters: Dimensions; graph: Receives owned blocks and count.
 * Returns: True on valid input; false with no owned blocks remaining.
 */
static bool blocks_read(const char *path, const kh_parameters_t *parameters, kh_graph_t *graph) {
    FILE *file = fopen(path, "r");
    uint64_t count = 0;

    // Bound compact descriptors before allocating any field or matching arrays.
    if (file == NULL) {
        return false;
    }

    // Descriptor count cannot exceed the split budget.
    if (fscanf(file, "%" SCNu64, &count) != 1 || count == 0 || count > parameters->budget ||
        count > SIZE_MAX / sizeof(kh_request_block_t)) {
        fclose(file);
        return false;
    }
    kh_request_block_t *blocks = calloc((size_t)count, sizeof *blocks);
    uint64_t stripes = 0;
    uint64_t cosets = 0;
    bool valid = blocks != NULL;

    // Each block describes repeated consecutive cosets with a common stripe count.
    for (uint64_t index = 0; valid && index < count; ++index) {
        uint64_t a;
        uint64_t copies;

        // Check multiplication bounds through the total budget before evaluating a*copies.
        if (fscanf(file, "%" SCNu64 " %" SCNu64, &a, &copies) != 2 || a == 0 || a > parameters->p ||
            copies == 0 || copies > parameters->budget / a || stripes + a * copies > parameters->budget) {
            valid = false;
            break;
        }
        blocks[index].first = stripes * parameters->f;
        blocks[index].coset = (uint32_t)cosets;
        blocks[index].copies = (uint32_t)copies;
        blocks[index].stripes = (uint16_t)a;
        stripes += a * copies;
        cosets += copies;
    }
    char trailing;

    // A descriptor file is one complete object with no trailing tokens.
    if (valid && (cosets + 1 > parameters->q - 1 || fscanf(file, " %c", &trailing) == 1 || ferror(file))) {
        valid = false;
    }
    fclose(file);

    // Ownership transfers only for a completely validated graph.
    if (!valid) {
        free(blocks);
        return false;
    }
    graph->blocks = blocks;
    graph->block_count = (uint32_t)count;
    graph->count = (uint32_t)(stripes * parameters->f);
    return true;
}

/*
 * Stream packed little-bit-first choices or Hall membership to a file.
 * Parameters: file: Writable stream; graph: Dimensions; matching: Final state; hall: Select Hall bitmap.
 * Returns: True after writing all bytes, including zero padding; false on I/O failure.
 */
static bool packed_write(FILE *file, const kh_graph_t *graph, const kh_matching_t *matching, bool hall) {
    bool obstructed = matching->matched < graph->count;
    uint32_t maximum = graph->field->parameters.f - 1 + obstructed;
    uint32_t bits = 0;
    uint64_t accumulator = 0;
    uint32_t available = 0;

    // Success uses indices 0..F-1; an obstruction reserves zero for unmatched requests.
    while (maximum != 0) {
        ++bits;
        maximum >>= 1;
    }

    // Hall membership has exactly one bit per request.
    if (hall) {
        bits = 1;
    }

    // Stream encoding avoids a second full matching payload in memory.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        uint32_t value = matching->distance[u];

        // Selected choices were retained during augmentation, avoiding a neighbor rescan.
        if (!hall) {
            value = matching->left[u] == UINT32_MAX ? 0 : matching->choice[u] + obstructed;
        }
        accumulator |= (uint64_t)value << available;
        available += bits;

        // Drain complete bytes in low-bit-first order.
        while (available >= 8) {

            // Any write failure aborts publication of this payload.
            if (fputc((int)(accumulator & 255), file) == EOF) {
                return false;
            }
            accumulator >>= 8;
            available -= 8;
        }
    }
    return available == 0 || fputc((int)accumulator, file) != EOF;
}

// Optional checkpoint control is kept outside the mathematical search state.
typedef struct {
    const char *path;
    unsigned char dp_hash[32];
    uint64_t interval_seconds;
    uint64_t stop_after_phases;
    uint64_t last_seconds;
    uint32_t saved;
    bool paused;
    bool handshake;
} checkpoint_control_t;

/*
 * Decode an exact 64-digit lowercase or uppercase SHA-256 hex string.
 * Parameters: text: Input ASCII hash; output: 32-byte digest destination.
 * Returns: True only for exactly 64 hexadecimal digits.
 */
static bool parse_hash(const char *text, unsigned char output[32]) {

    // Check length before accessing any fixed-position digits.
    if (strlen(text) != 64) {
        return false;
    }

    // Every pair contributes one byte of the DP artifact identity.
    for (uint32_t index = 0; index < 32; ++index) {
        unsigned char value = 0;

        // A malformed nibble invalidates the whole digest.
        for (uint32_t nibble = 0; nibble < 2; ++nibble) {
            unsigned char character = (unsigned char)text[index * 2 + nibble];
            uint32_t digit;

            // Accept both conventional hexadecimal letter cases.
            if (character >= '0' && character <= '9') {
                digit = character - '0';
            } else if (character >= 'a' && character <= 'f') {
                digit = character - 'a' + 10;
            } else if (character >= 'A' && character <= 'F') {
                digit = character - 'A' + 10;
            } else {
                return false;
            }
            value = (unsigned char)(value * 16 + digit);
        }
        output[index] = value;
    }
    return true;
}

/*
 * Read a monotonic whole-second clock for checkpoint cadence.
 * Parameters: output: Receives seconds since an unspecified monotonic epoch.
 * Returns: True when the Linux monotonic clock is available.
 */
static bool monotonic_seconds(uint64_t *output) {
    struct timespec current;

    // Wall-clock corrections never change a running checkpoint schedule.
    if (clock_gettime(CLOCK_MONOTONIC, &current) != 0) {
        return false;
    }
    *output = (uint64_t)current.tv_sec;
    return true;
}

/*
 * Save a fully committed phase when its interval or explicit pause is due.
 * Parameters: graph: Borrowed graph; matching: Committed phase; context: Mutable checkpoint_control_t; error: Diagnostic.
 * Returns: True to continue; false on I/O failure or an intentional checkpointed pause.
 */
static bool checkpoint_phase(const kh_graph_t *graph, const kh_matching_t *matching,
                             void *context, const char **error) {
    checkpoint_control_t *control = context;
    uint64_t now;

    // A failed clock read cannot safely substitute a false checkpoint interval.
    if (!monotonic_seconds(&now)) {
        *error = "cannot read checkpoint monotonic clock";
        return false;
    }
    bool stopping = control->stop_after_phases != 0 && matching->phases >= control->stop_after_phases;
    bool due = control->interval_seconds == 0 || now - control->last_seconds >= control->interval_seconds;

    // Report committed progress with one bounded JSON record per phase.
    printf("{\"done\":%u,\"total\":%u,\"checkpoint_done\":0,\"phase\":\"matching\",\"units\":\"requests\",\"heartbeat\":true}\n",
           matching->matched, graph->count);
    fflush(stdout);

    // Each saved image describes exactly one committed matching phase.
    if (due || stopping) {
        if (!kh_checkpoint_write(control->path, graph, matching, control->dp_hash, error)) {
            return false;
        }
        control->last_seconds = now;
        ++control->saved;
        fprintf(stderr, "checkpoint=%s phase=%" PRIu64 " matched=%u/%u\n",
                control->path, matching->phases, matching->matched, graph->count);

        // Keep the image immutable until the cluster has replicated its snapshot.
        if (control->handshake) {
            printf("{\"event\":\"checkpoint\",\"cursor\":%" PRIu64 "}\n", matching->phases);
            fflush(stdout);
            if (getchar() != '\n') {
                *error = "checkpoint acknowledgment missing";
                return false;
            }
        }
    }

    // A requested pause leaves the complete checkpoint ready for the next process.
    if (stopping) {
        control->paused = true;
        return false;
    }
    return true;
}

/*
 * Run a memory-admitted exact matching and publish its compact kernel payload.
 * Parameters: argc: Argument count; argv: Input strings described by help.
 * Returns: Zero for help/full matching, two for obstruction, one for errors.
 */
int main(int argc, char **argv) {

    // Empty invocation prints help without touching storage or the cluster.
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }

    // Four positional arguments identify dimensions, compact requests, and a fresh output.
    if (argc < 5) {
        help();
        return 1;
    }
    uint64_t p;
    uint64_t r;
    uint64_t threads = 1;
    uint64_t start = 1;
    uint64_t maximum = UINT64_C(2147483648);
    const char *poly_text = NULL;
    const char *checkpoint_path = NULL;
    const char *resume_path = NULL;
    const char *hash_text = NULL;
    uint64_t checkpoint_seconds = 1800;
    uint64_t stop_after_phases = 0;
    bool checkpoint_handshake = false;
    const char *error = NULL;
    kh_parameters_t parameters;

    // Reject dimension truncation before calling the shared validator.
    if (!kh_parse_u64(argv[1], &p) || !kh_parse_u64(argv[2], &r) || p > UINT32_MAX || r > UINT32_MAX ||
        !kh_parameters((uint32_t)p, (uint32_t)r, &parameters, &error)) {
        fprintf(stderr, "kh_match_kernel: invalid dimensions\n");
        return 1;
    }

    // Operational flags are explicit pairs; unknown flags never silently change a run.
    for (int index = 5; index < argc; ++index) {
        const char *option = argv[index];

        // The cluster handshake is the sole valueless control.
        if (!strcmp(option, "--checkpoint-handshake")) {
            checkpoint_handshake = true;
            continue;
        }

        // Every supported flag requires a value.
        if (++index >= argc) {
            help();
            return 1;
        }

        // A supplied polynomial is pinned for this attempt.
        if (!strcmp(option, "--poly")) {
            poly_text = argv[index];
            continue;
        }

        // Checkpoint paths and DP hash are textual controls, not decimal values.
        if (!strcmp(option, "--checkpoint")) {
            checkpoint_path = argv[index];
            continue;
        }
        if (!strcmp(option, "--resume")) {
            resume_path = argv[index];
            continue;
        }
        if (!strcmp(option, "--dp-hash")) {
            hash_text = argv[index];
            continue;
        }
        uint64_t value;

        // Unsigned decimal parsing rejects malformed operational arguments.
        if (!kh_parse_u64(argv[index], &value)) {
            return 1;
        }

        // Field construction and parallel BFS use the same pinned thread budget.
        if (!strcmp(option, "--threads")) {
            threads = value;
        } else if (!strcmp(option, "--start")) {
            start = value;
        } else if (!strcmp(option, "--max-bytes")) {
            maximum = value;
        } else if (!strcmp(option, "--checkpoint-seconds")) {
            checkpoint_seconds = value;
        } else if (!strcmp(option, "--stop-after-phases")) {
            stop_after_phases = value;
        } else {
            help();
            return 1;
        }
    }

    // Bound metadata before allocation and reject a zero memory ceiling.
    if (resume_path != NULL && checkpoint_path == NULL) {
        checkpoint_path = resume_path;
    }
    unsigned char dp_hash[32] = {0};

    // A restorable checkpoint must bind an explicit field and exact DP bytes.
    if ((checkpoint_path != NULL || resume_path != NULL) &&
        (poly_text == NULL || hash_text == NULL || !parse_hash(hash_text, dp_hash))) {
        fprintf(stderr, "kh_match_kernel: checkpoints require --poly and --dp-hash\n");
        return 1;
    }
    if (checkpoint_handshake && checkpoint_path == NULL) {
        fprintf(stderr, "kh_match_kernel: handshake requires a checkpoint\n");
        return 1;
    }
    if (stop_after_phases != 0 && checkpoint_path == NULL) {
        fprintf(stderr, "kh_match_kernel: --stop-after-phases requires a checkpoint\n");
        return 1;
    }

    // Bound metadata before allocation and reject a zero memory ceiling.
    if (threads == 0 || threads > 1024 || start > UINT32_MAX || maximum == 0) {
        fprintf(stderr, "kh_match_kernel: invalid resource controls\n");
        return 1;
    }
    kh_graph_t graph = {0};

    // The kernel validates graph budgets independently of the Python DP decoder.
    if (!blocks_read(argv[3], &parameters, &graph)) {
        fprintf(stderr, "kh_match_kernel: invalid request blocks\n");
        return 1;
    }
    uint64_t descriptors = (uint64_t)graph.block_count * sizeof(kh_request_block_t);
    uint64_t reserve = UINT64_C(67108864);
    uint64_t state_peak = 8 * (uint64_t)parameters.q + 24 * (uint64_t)graph.count +
                          ((uint64_t)parameters.q + 7) / 8 + descriptors +
                          UINT64_C(8388608) * threads +
                          (threads > 1 ? 4 * (uint64_t)graph.count +
                          8 * (((uint64_t)parameters.q + 63) / 64) : 0) + reserve;
    uint64_t field_peak = 4 * (uint64_t)parameters.q + 4 * (uint64_t)parameters.budget * threads +
                          UINT64_C(8388608) * threads + descriptors + reserve;
    uint64_t required = state_peak > field_peak ? state_peak : field_peak;
    bool success = false;
    bool paused = false;
    checkpoint_control_t checkpoint = {0};
    checkpoint.path = checkpoint_path;
    checkpoint.interval_seconds = checkpoint_seconds;
    checkpoint.stop_after_phases = stop_after_phases;
    checkpoint.handshake = checkpoint_handshake;
    memcpy(checkpoint.dp_hash, dp_hash, sizeof dp_hash);

    // Checkpoint timing includes field construction as part of the attempt.
    if (checkpoint_path != NULL && !monotonic_seconds(&checkpoint.last_seconds)) {
        fprintf(stderr, "kh_match_kernel: cannot read monotonic clock\n");
        free((void *)graph.blocks);
        return 1;
    }
    kh_field_t field = {0};
    kh_matching_t matching = {0};
    uint16_t polynomial[32] = {0};
    uint32_t candidate = 0;
    uint32_t hall_left = 0;
    uint32_t hall_right = 0;

    // Admission includes matching arrays, Hall scratch, field-building peak, and headroom.
    if (required > maximum || maximum > SIZE_MAX) {
        fprintf(stderr, "kh_match_kernel: requires at least %" PRIu64 " bytes; limit=%" PRIu64 "\n", required, maximum);
        goto cleanup;
    }
    struct rlimit limit;

    // An address-space ceiling makes admission a hard bound rather than an estimate alone.
    if (getrlimit(RLIMIT_AS, &limit) != 0) {
        goto cleanup;
    }
    rlim_t requested = (rlim_t)maximum;

    // Respect a pre-existing tighter operator or system limit.
    if (limit.rlim_cur == RLIM_INFINITY || requested < limit.rlim_cur) {
        limit.rlim_cur = requested;
    }

    // Failure to install a limit must not launch an unbounded computation.
    if (setrlimit(RLIMIT_AS, &limit) != 0) {
        goto cleanup;
    }

    // An explicit polynomial is checked with the fixed generator X.
    if (poly_text != NULL) {

        // Invalid pinned polynomials are operational errors, not Hall obstructions.
        if (!polynomial_parse(poly_text, &parameters, polynomial) || !kh_primitive(&parameters, polynomial)) {
            fprintf(stderr, "kh_match_kernel: polynomial is not primitive with generator X\n");
            goto cleanup;
        }
    } else if (!kh_generate_polynomial(&parameters, (uint32_t)start, polynomial, &candidate)) {
        fprintf(stderr, "kh_match_kernel: primitive polynomial candidates exhausted\n");
        goto cleanup;
    }

    // Build one shared field using the existing production implementation.
    if (!kh_build_field(&parameters, polynomial, (uint32_t)threads, maximum - descriptors, &field, &error)) {
        fprintf(stderr, "kh_match_kernel: %s\n", error);
        goto cleanup;
    }
    graph.field = &field;

    // Matching arrays are allocated only after the temporary field-build counters are released.
    if (!kh_matching_create(&graph, &matching)) {
        fprintf(stderr, "kh_match_kernel: matching allocation failed\n");
        goto cleanup;
    }
    // A restored state is checked against the rebuilt primitive-X graph before any search.
    if (resume_path != NULL && !kh_checkpoint_read(resume_path, &graph, &matching, dp_hash, &error)) {
        fprintf(stderr, "kh_match_kernel: %s\n", error);
        goto cleanup;
    }
    kh_phase_callback_t callback = checkpoint_path == NULL ? NULL : checkpoint_phase;

    // Every callback observes a fully committed phase, never an in-flight path.
    if (!kh_matching_solve_with_hook(&graph, &matching, (uint32_t)threads,
                                     callback, &checkpoint, &error)) {
        paused = checkpoint.paused;

        // Intentional pause has already saved the complete phase state.
        if (!paused) {
            fprintf(stderr, "kh_match_kernel: %s\n", error);
        }
        goto cleanup;
    }
    bool obstructed = matching.matched < graph.count;

    // Only a verified exact Hall deficiency permits an obstruction result.
    if (obstructed && !kh_matching_hall(&graph, &matching, &hall_left, &hall_right)) {
        fprintf(stderr, "kh_match_kernel: Hall extraction failed\n");
        goto cleanup;
    }
    int descriptor = open(argv[4], O_WRONLY | O_CREAT | O_EXCL, 0600);

    // Never overwrite a previous attempt or an existing caller file.
    if (descriptor < 0) {
        perror("kh_match_kernel: output");
        goto cleanup;
    }
    FILE *file = fdopen(descriptor, "wb");

    // A failed stream conversion still owns the raw descriptor.
    if (file == NULL) {
        close(descriptor);
        unlink(argv[4]);
        goto cleanup;
    }
    success = packed_write(file, &graph, &matching, false);

    // Obstructions append a separate zero-padded left-set bitmap.
    if (success && obstructed) {
        success = packed_write(file, &graph, &matching, true);
    }
    success = success && fflush(file) == 0 && fsync(fileno(file)) == 0;

    // Closing a failed stream is mandatory before removing its partial payload.
    if (fclose(file) != 0) {
        success = false;
    }

    // Failed payloads are never reported as usable results.
    if (!success) {
        unlink(argv[4]);
        goto cleanup;
    }
    printf("{\"p\":%u,\"r\":%u,\"candidate\":%u,\"polynomial\":[", parameters.p, parameters.r, candidate);

    // Metadata is small; large choices remain in the streamed binary payload.
    for (uint32_t index = 0; index <= parameters.r; ++index) {
        printf("%s%u", index == 0 ? "" : ",", polynomial[index]);
    }
    printf("],\"status\":%u,\"required\":%u,\"matched\":%u,\"phases\":%" PRIu64
           ",\"scans\":%" PRIu64 ",\"memory_required\":%" PRIu64 ",\"hall_left\":%u,\"hall_right\":%u}\n",
           obstructed, graph.count, matching.matched, matching.phases, matching.scans, required, hall_left, hall_right);

cleanup:
    free((void *)graph.blocks);
    kh_free_field(&field);
    uint32_t matched = matching.matched;
    kh_matching_free(&matching);
    kh_resource_print("solver", 0);
    return success ? (matched == graph.count ? 0 : 2) : (paused ? 3 : 1);
}
