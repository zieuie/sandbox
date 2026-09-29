#define _POSIX_C_SOURCE 200809L

#include "kh_solver.h"
#include "kh_threads.h"
#include "kh_progress.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

// Local restart metadata committed only after both mapped arrays are durable.
typedef struct {
    unsigned char magic[8];
    uint32_t version;
    uint32_t p;
    uint32_t r;
    uint32_t budget;
    uint32_t tile_side;
    uint64_t next_tile;
    uint64_t total_tiles;
    uint64_t value_bytes;
    uint64_t choice_bytes;
} checkpoint_t;

// One reconstructed transition in final split order.
typedef struct {
    uint16_t a;
    uint16_t b;
    uint16_t t;
} output_step_t;

// Parsed controls for one local DP invocation.
typedef struct {
    uint32_t p;
    uint32_t r;
    uint32_t tile_side;
    uint32_t threads;
    uint64_t checkpoint_seconds;
    uint64_t progress_milliseconds;
    uint64_t max_state_bytes;
    uint64_t max_visits;
    uint64_t stop_after_tiles;
    const char *work_directory;
    const char *output_path;
    bool reset;
    bool raw_transitions;
    bool checkpoint_handshake;
} options_t;

// Shared immutable dimensions and disjoint per-cell writable arrays.
typedef struct {
    uint64_t *values;
    uint32_t *choices;
    size_t side;
    uint16_t p;
    const kh_transition_t *transitions;
    uint32_t transition_count;
} dp_context_t;

static volatile sig_atomic_t stop_requested = 0;

/*
 * Print local DP usage, restart behavior, and a runnable example.
 *
 * Parameters: none.
 *
 * Returns:
 *   No value; writes help to stdout.
 */
static void help(void) {
    puts("Compute the paper's exact DP with file-backed tiled state.\n"
         "Usage: ./kh_dp_local PRIME ODD_DEGREE --work-dir DIR -o FILE [OPTIONS]\n"
         "Example: ./kh_dp_local 5 3 --work-dir state/5_3 -o split_5_3.khdp.json\n"
         "Options:\n"
         "  --checkpoint-handshake    agent-only: pause for snapshot acknowledgment on stdin\n"
         "  --raw-transitions         unpruned reference scan; checkpoints compatible\n"
         "  --threads N               pinned worker count, default 1\n"
         "  --tile-side N             logical tile side, default 4096\n"
         "  --progress-milliseconds N periodic heartbeat, default 10000\n"
         "  --checkpoint-seconds N    durable checkpoint target, default 1800\n"
         "  --max-state-bytes N       admission limit, default 17179869184\n"
         "  --max-visits N            work admission limit, default 5000000000\n"
         "  --stop-after-tiles N       test stop after N tiles in this invocation\n"
         "  --reset                    discard this work directory's DP state\n"
         "SIGINT/SIGTERM finishes the active tile, checkpoints, and exits 75.\n"
         "Existing output files are never overwritten. No arguments prints help.");
}

/*
 * Print an error associated with the local DP command.
 *
 * Parameters:
 *   message: Input diagnostic text.
 *
 * Returns:
 *   Exit status one.
 */
static int fail(const char *message) {
    fprintf(stderr, "kh_dp_local: %s\n", message);
    return 1;
}

/*
 * Record a request to stop at the next complete tile boundary.
 *
 * Parameters:
 *   signal_number: Delivered signal number, ignored after recording the request.
 *
 * Returns:
 *   No value; updates only a signal-safe scalar flag.
 */
static void request_stop(int signal_number) {
    (void)signal_number;
    stop_requested = 1;
}

/*
 * Join a directory and fixed filename into caller storage.
 *
 * Parameters:
 *   output: Writable destination buffer.
 *   capacity: Number of bytes available in output.
 *   directory: Input directory path without any ownership transfer.
 *   filename: Input basename.
 *
 * Returns:
 *   True when the joined path fits; false when it would be truncated.
 */
static bool join_path(
    char *output,
    size_t capacity,
    const char *directory,
    const char *filename
) {
    int length = snprintf(output, capacity, "%s/%s", directory, filename);
    return length >= 0 && (size_t)length < capacity;
}

/*
 * Write an entire fixed-size buffer to a file descriptor.
 *
 * Parameters:
 *   descriptor: Open writable file descriptor.
 *   data: Input bytes.
 *   size: Number of bytes to write.
 *
 * Returns:
 *   True after all bytes are written; false on an I/O error.
 */
static bool write_all(int descriptor, const void *data, size_t size) {
    const unsigned char *cursor = data;

    // Retry short writes until the complete object is durable in the file cache.
    while (size > 0) {
        ssize_t written = write(descriptor, cursor, size);

        // Interrupted writes can be retried without losing progress.
        if (written < 0 && errno == EINTR) {
            continue;
        }

        // Any other failed or zero write cannot complete the object.
        if (written <= 0) {
            return false;
        }
        cursor += written;
        size -= (size_t)written;
    }
    return true;
}

/*
 * Read one complete checkpoint object.
 *
 * Parameters:
 *   path: Input checkpoint path.
 *   output: Output structure receiving the file contents.
 *
 * Returns:
 *   One when read successfully, zero when absent, and minus one on invalid I/O.
 */
static int read_checkpoint(const char *path, checkpoint_t *output) {
    int descriptor = open(path, O_RDONLY);

    // A missing checkpoint denotes a fresh work directory.
    if (descriptor < 0 && errno == ENOENT) {
        return 0;
    }

    // Other open errors prevent safe restart.
    if (descriptor < 0) {
        return -1;
    }
    size_t remaining = sizeof *output;
    unsigned char *cursor = (unsigned char *)output;

    // Require exactly one complete metadata object.
    while (remaining > 0) {
        ssize_t received = read(descriptor, cursor, remaining);

        // Retry an interrupted read.
        if (received < 0 && errno == EINTR) {
            continue;
        }

        // Truncation or another error invalidates the checkpoint.
        if (received <= 0) {
            close(descriptor);
            return -1;
        }
        cursor += received;
        remaining -= (size_t)received;
    }
    unsigned char extra;
    ssize_t trailing = read(descriptor, &extra, 1);
    int close_status = close(descriptor);

    // Reject trailing metadata and deferred close failures.
    if (trailing != 0 || close_status != 0) {
        return -1;
    }
    return 1;
}

/*
 * Flush DP arrays and atomically publish their restart metadata.
 *
 * Parameters:
 *   values: Shared value mapping.
 *   value_bytes: Length of values in bytes.
 *   choices: Shared choice mapping.
 *   choice_bytes: Length of choices in bytes.
 *   checkpoint: Metadata whose next_tile is the first unfinished tile.
 *   path: Final checkpoint path.
 *   temporary_path: Temporary checkpoint path in the same directory.
 *
 * Returns:
 *   True when arrays and metadata are durable; false on an I/O error.
 */
static bool commit_checkpoint(
    uint64_t *values,
    size_t value_bytes,
    uint32_t *choices,
    size_t choice_bytes,
    const checkpoint_t *checkpoint,
    const char *path,
    const char *temporary_path
) {

    // Flush both mappings before advancing the published tile cursor.
    if (msync(values, value_bytes, MS_SYNC) != 0 ||
        msync(choices, choice_bytes, MS_SYNC) != 0) {
        return false;
    }
    int descriptor = open(temporary_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);

    // Do not disturb the previous checkpoint when the temporary file cannot open.
    if (descriptor < 0) {
        return false;
    }
    bool success = write_all(descriptor, checkpoint, sizeof *checkpoint);

    // Force the metadata bytes before publishing the new filename.
    if (success && fsync(descriptor) != 0) {
        success = false;
    }

    // Preserve a close error as a failed checkpoint.
    if (close(descriptor) != 0) {
        success = false;
    }

    // Atomically replace the old cursor only after a successful write.
    if (success && rename(temporary_path, path) != 0) {
        success = false;
    }

    // Persist the atomic rename so a power loss cannot roll back the directory entry.
    if (success) {
        char parent[4096];
        size_t length = strlen(path);

        // Check the local path buffer before locating its parent directory.
        if (length >= sizeof parent) {
            success = false;
        } else {
            memcpy(parent, path, length + 1);
            char *slash = strrchr(parent, '/');

            // Work paths normally have a directory; also support a bare filename.
            if (slash == NULL) {
                strcpy(parent, ".");
            } else if (slash == parent) {
                slash[1] = '\0';
            } else {
                *slash = '\0';
            }
            int directory = open(parent, O_RDONLY | O_DIRECTORY);

            // A failed directory flush must remain a visible checkpoint failure.
            if (directory < 0) {
                success = false;
            } else {
                if (fsync(directory) != 0) {
                    success = false;
                }
                if (close(directory) != 0) {
                    success = false;
                }
            }
        }
    }

    // Remove an unpublished temporary metadata file.
    if (!success) {
        unlink(temporary_path);
    }
    return success;
}

/*
 * Open, size, and map one shared state array.
 *
 * Parameters:
 *   path: Input state-file path.
 *   bytes: Required nonzero file and mapping length.
 *   fresh: Whether the file must be newly created and zero initialized.
 *   descriptor_output: Output open descriptor owned by the caller.
 *   mapping_output: Output shared mapping owned by the caller.
 *
 * Returns:
 *   True on success; false on file, size, or mapping errors.
 */
static bool map_state_file(
    const char *path,
    size_t bytes,
    bool fresh,
    int *descriptor_output,
    void **mapping_output
) {
    int flags = O_RDWR;

    // A new calculation must not silently reuse orphaned state.
    if (fresh) {
        flags |= O_CREAT | O_EXCL;
    }
    int descriptor = open(path, flags, 0600);

    // Report open and stale-file errors to the caller.
    if (descriptor < 0) {
        return false;
    }

    // Allocate the exact sparse state-file length for a fresh calculation.
    if (fresh && ftruncate(descriptor, (off_t)bytes) != 0) {
        close(descriptor);
        return false;
    }
    struct stat status;

    // A resumed mapping must exactly match the checkpoint dimensions.
    if (fstat(descriptor, &status) != 0 || status.st_size < 0 ||
        (uint64_t)status.st_size != bytes) {
        close(descriptor);
        return false;
    }
    void *mapping = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, descriptor, 0);

    // MAP_FAILED is not a usable state pointer.
    if (mapping == MAP_FAILED) {
        close(descriptor);
        return false;
    }
    *descriptor_output = descriptor;
    *mapping_output = mapping;
    return true;
}

/*
 * Hold immutable-at-this-boundary state until the agent has copied its snapshot.
 * Parameters:
 *   cursor: Committed next-tile cursor advertised to the owning agent.
 * Returns: True for a newline acknowledgment; false for EOF or a protocol/I/O error.
 */
static bool snapshot_handshake(uint64_t cursor) {

    // One stdio call keeps this event separate from concurrent heartbeat records.
    if (printf("{\"event\":\"checkpoint\",\"cursor\":%" PRIu64 "}\n", cursor) < 0 ||
        fflush(stdout) != 0) {
        return false;
    }
    unsigned char acknowledgment;
    ssize_t received;

    // Signals only latch stop; the active snapshot still needs its acknowledgment.
    do {
        received = read(STDIN_FILENO, &acknowledgment, 1);
    } while (received < 0 && errno == EINTR);
    return received == 1 && acknowledgment == '\n';
}

/*
 * Return monotonic whole seconds for checkpoint scheduling.
 *
 * Parameters: none.
 *
 * Returns:
 *   Monotonic seconds, or zero when the system clock call fails.
 */
static uint64_t monotonic_seconds(void) {
    struct timespec value;

    // A monotonic clock prevents wall-clock changes from moving the deadline.
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        return 0;
    }
    return (uint64_t)value.tv_sec;
}

/*
 * Read monotonic fractional seconds for phase measurements.
 * Parameters: none.
 * Returns: Seconds from an unspecified origin, or zero if the clock fails.
 */
static double elapsed_clock(void) {
    struct timespec value;

    // Use a monotonic origin for duration differences.
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        return 0;
    }
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

/*
 * Parse the command line into local DP controls.
 *
 * Parameters:
 *   argc: Number of command-line arguments.
 *   argv: Input argument vector.
 *   output: Output options filled on success.
 *
 * Returns:
 *   True for a complete valid command; false after a diagnostic-worthy syntax error.
 */
static bool parse_options(int argc, char **argv, options_t *output) {

    // Initialize operational limits before applying explicit overrides.
    *output = (options_t){0};
    output->tile_side = 4096;
    output->threads = 1;
    output->checkpoint_seconds = 1800;
    output->progress_milliseconds = 10000;
    output->max_state_bytes = UINT64_C(17179869184);
    output->max_visits = UINT64_C(5000000000);
    uint64_t value;

    // Parse the required prime and odd degree first.
    if (argc < 3 || !kh_parse_u64(argv[1], &value) || value > UINT32_MAX) {
        return false;
    }
    output->p = (uint32_t)value;

    // Store the degree only after checking its public input width.
    if (!kh_parse_u64(argv[2], &value) || value > UINT32_MAX) {
        return false;
    }
    output->r = (uint32_t)value;

    // Parse all named paths and resource controls.
    for (int index = 3; index < argc; ++index) {

        // Record the durable work directory.
        if (!strcmp(argv[index], "--work-dir") && index + 1 < argc) {
            output->work_directory = argv[++index];
            continue;
        }

        // Record the final artifact path.
        if (!strcmp(argv[index], "-o") && index + 1 < argc) {
            output->output_path = argv[++index];
            continue;
        }

        // Let the owning agent snapshot committed state before the next tile mutates it.
        if (!strcmp(argv[index], "--checkpoint-handshake")) {
            output->checkpoint_handshake = true;
            continue;
        }

        // Keep an unpruned reference without changing checkpoint or artifact identity.
        if (!strcmp(argv[index], "--raw-transitions")) {
            output->raw_transitions = true;
            continue;
        }

        // Allow an explicit reset of known state files.
        if (!strcmp(argv[index], "--reset")) {
            output->reset = true;
            continue;
        }

        // Parse each numeric option through the same strict decimal helper.
        if (index + 1 < argc &&
            (!strcmp(argv[index], "--threads") ||
             !strcmp(argv[index], "--tile-side") ||
             !strcmp(argv[index], "--checkpoint-seconds") ||
             !strcmp(argv[index], "--progress-milliseconds") ||
             !strcmp(argv[index], "--max-state-bytes") ||
             !strcmp(argv[index], "--max-visits") ||
             !strcmp(argv[index], "--stop-after-tiles"))) {
            const char *name = argv[index++];

            // Reject malformed option values before narrowing any field.
            if (!kh_parse_u64(argv[index], &value)) {
                return false;
            }

            // Apply the parsed value to its selected control.
            if (!strcmp(name, "--threads")) {

                // Reject zero and values that cannot fit the worker interface.
                if (value == 0 || value > UINT32_MAX) {
                    return false;
                }
                output->threads = (uint32_t)value;
            } else if (!strcmp(name, "--tile-side")) {
                if (value == 0 || value > UINT32_MAX) {
                    return false;
                }
                output->tile_side = (uint32_t)value;
            } else if (!strcmp(name, "--progress-milliseconds")) {

                // Bound heartbeat timing to positive intervals of at most one day.
                if (value == 0 || value > 86400000) {
                    return false;
                }
                output->progress_milliseconds = value;
            } else if (!strcmp(name, "--checkpoint-seconds")) {
                output->checkpoint_seconds = value;
            } else if (!strcmp(name, "--max-state-bytes")) {
                output->max_state_bytes = value;
            } else if (!strcmp(name, "--max-visits")) {
                output->max_visits = value;
            } else {
                output->stop_after_tiles = value;
            }
            continue;
        }
        return false;
    }
    return output->work_directory != NULL && output->output_path != NULL;
}

/*
 * Evaluate one DP cell with the paper's exact transition and tie order.
 *
 * Parameters:
 *   values: Input/output row-major value table.
 *   choices: Output row-major transition identifiers.
 *   side: Number of cells in one complete table row.
 *   p: Prime for encoding stable historical choice IDs.
 *   transitions: Immutable transitions in ascending a,b,t order.
 *   transition_count: Number of readable transitions.
 *   u: Positive stripe-budget coordinate.
 *   v: Positive special-set-budget coordinate.
 *
 * Returns:
 *   No value; writes the optimum and earliest maximizing transition for (u,v).
 */
static void fill_cell(
    uint64_t *values,
    uint32_t *choices,
    size_t side,
    uint16_t p,
    const kh_transition_t *transitions,
    uint32_t transition_count,
    uint32_t u,
    uint32_t v
) {
    size_t cell = (size_t)u * side + v;

    // Replay uncommitted cells from zero, ignoring partially persisted choices.
    values[cell] = 0;
    choices[cell] = 0;

    // Compare transitions in fixed ascending a,b,t order.
    for (uint32_t transition_index = 0;
         transition_index < transition_count;
         ++transition_index) {
        kh_transition_t transition = transitions[transition_index];
        uint32_t delta_u = transition.a * transition.t;
        uint32_t delta_v = transition.b * transition.t;

        // Only affordable transitions participate in the recurrence.
        if (delta_u > u || delta_v > v) {
            continue;
        }
        size_t predecessor = (size_t)(u - delta_u) * side + (v - delta_v);
        uint64_t candidate = values[predecessor] + transition.gain;

        // Strict improvement retains the earliest transition on a tie.
        if (candidate > values[cell]) {
            values[cell] = candidate;
            choices[cell] = kh_transition_id(p, &transition);
        }
    }
}

/*
 * Adapt shared DP state to the reusable rectangular worker-pool interface.
 * Parameters:
 *   context: Input dp_context_t valid for the pool's lifetime.
 *   u: Stripe coordinate owned exclusively by this cell invocation with v.
 *   v: Symbol coordinate owned exclusively by this cell invocation with u.
 * Returns: No value; evaluates and writes exactly one DP cell.
 */
static void evaluate_cell(void *context, uint32_t u, uint32_t v) {
    dp_context_t *dp = context;
    fill_cell(dp->values, dp->choices, dp->side, dp->p, dp->transitions, dp->transition_count, u, v);
}

/*
 * Write the completed split as a run-length draft artifact without overwriting.
 *
 * Parameters:
 *   path: Output path that must not exist.
 *   parameters: Mathematical input and derived dimensions.
 *   theta: Exact optimum at the full budget.
 *   steps: Reconstructed steps in choice-following order.
 *   count: Number of readable steps.
 *
 * Returns:
 *   True after a complete close; false on create or write failure.
 */
static bool write_artifact(
    const char *path,
    const kh_parameters_t *parameters,
    uint64_t theta,
    const output_step_t *steps,
    uint32_t count
) {
    int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);

    // Never replace a previous result or comparison run.
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");

    // Preserve the exclusive file only when it can be streamed safely.
    if (file == NULL) {
        close(descriptor);
        unlink(path);
        return false;
    }
    bool success = true;

    // Emit a versioned, run-length representation for this solver milestone.
    if (fprintf(file,
                "{\n  \"format\": \"KHDP2-draft\",\n  \"p\": %u,\n  \"r\": %u,\n"
                "  \"q\": %" PRIu32 ",\n  \"f\": %" PRIu32 ",\n"
                "  \"budget\": %" PRIu32 ",\n  \"theta\": %" PRIu64 ",\n  \"runs\": [",
                parameters->p,
                parameters->r,
                parameters->q,
                parameters->f,
                parameters->budget,
                theta) < 0) {
        success = false;
    }
    uint32_t index = 0;
    bool first = true;

    // Combine only consecutive identical reconstructed choices.
    while (success && index < count) {
        output_step_t step = steps[index];
        uint32_t repeat = 1;

        // Extend the current run without reordering the split.
        while (index + repeat < count &&
               steps[index + repeat].a == step.a &&
               steps[index + repeat].b == step.b &&
               steps[index + repeat].t == step.t) {
            ++repeat;
        }

        // Separate JSON run records after the first entry.
        if (fprintf(file,
                    "%s\n    {\"a\": %u, \"b\": %u, \"t\": %u, \"repeat\": %u}",
                    first ? "" : ",",
                    step.a,
                    step.b,
                    step.t,
                    repeat) < 0) {
            success = false;
        }
        first = false;
        index += repeat;
    }

    // Close the JSON document and force buffered output.
    if (success && fprintf(file, "\n  ]\n}\n") < 0) {
        success = false;
    }

    // A close error can report a deferred filesystem failure.
    if (fclose(file) != 0) {
        success = false;
    }

    // Do not retain a partial artifact under the requested final name.
    if (!success) {
        unlink(path);
    }
    return success;
}

/*
 * Execute the exact recurrence, reconstruct its split, and save the artifact.
 *
 * Parameters:
 *   options: Parsed command controls.
 *
 * Returns:
 *   Zero on completion, 75 after an intentional checkpointed stop, and one on error.
 */
static int run(const options_t *options) {
    kh_parameters_t parameters;
    kh_resource_estimate_t estimate;
    const char *error;

    // Validate the mathematics and obtain checked state dimensions.
    if (!kh_parameters(options->p, options->r, &parameters, &error) ||
        !kh_estimate_resources(&parameters, options->tile_side, &estimate, &error)) {
        return fail(error);
    }

    // Enforce leader-adjustable admission limits before touching large files.
    if (estimate.state_bytes > options->max_state_bytes) {
        return fail("estimated DP state exceeds --max-state-bytes");
    }

    // Refuse work that was not explicitly admitted by its visit budget.
    if (estimate.estimated_visits_overflow ||
        estimate.estimated_visits > options->max_visits) {
        return fail("estimated transition visits exceed --max-visits");
    }

    // mmap lengths and local files must fit the host interfaces.
    if (estimate.value_bytes > SIZE_MAX || estimate.choice_bytes > SIZE_MAX ||
        estimate.value_bytes > INT64_MAX || estimate.choice_bytes > INT64_MAX) {
        return fail("state files exceed local size interfaces");
    }

    // Preserve completed artifacts unless the caller chooses another path.
    if (access(options->output_path, F_OK) == 0) {
        return fail("output already exists");
    }

    // Create or reuse the private work directory.
    if (mkdir(options->work_directory, 0700) != 0 && errno != EEXIST) {
        return fail("cannot create work directory");
    }
    char value_path[4096];
    char choice_path[4096];
    char checkpoint_path[4096];
    char temporary_path[4096];

    // Construct every fixed state filename without truncation.
    if (!join_path(value_path, sizeof value_path, options->work_directory, "values.bin") ||
        !join_path(choice_path, sizeof choice_path, options->work_directory, "choices.bin") ||
        !join_path(checkpoint_path, sizeof checkpoint_path, options->work_directory, "checkpoint.bin") ||
        !join_path(temporary_path, sizeof temporary_path, options->work_directory, "checkpoint.tmp")) {
        return fail("work directory path is too long");
    }

    // Reset only the known files inside this calculation's work directory.
    if (options->reset) {
        unlink(value_path);
        unlink(choice_path);
        unlink(checkpoint_path);
        unlink(temporary_path);
    }
    uint64_t tile_rows = ((uint64_t)parameters.budget + options->tile_side - 1) /
                         options->tile_side;
    uint64_t total_tiles = tile_rows * tile_rows;
    checkpoint_t checkpoint = {0};
    int checkpoint_status = read_checkpoint(checkpoint_path, &checkpoint);

    // Stop when existing metadata is unreadable rather than guessing state validity.
    if (checkpoint_status < 0) {
        return fail("cannot read checkpoint metadata");
    }
    bool fresh = checkpoint_status == 0;

    // Initialize or validate the local restart identity.
    if (fresh) {
        memcpy(checkpoint.magic, "KHDPCHK1", 8);
        checkpoint.version = 1;
        checkpoint.p = parameters.p;
        checkpoint.r = parameters.r;
        checkpoint.budget = parameters.budget;
        checkpoint.tile_side = options->tile_side;
        checkpoint.next_tile = 0;
        checkpoint.total_tiles = total_tiles;
        checkpoint.value_bytes = estimate.value_bytes;
        checkpoint.choice_bytes = estimate.choice_bytes;
    } else if (memcmp(checkpoint.magic, "KHDPCHK1", 8) ||
               checkpoint.version != 1 ||
               checkpoint.p != parameters.p ||
               checkpoint.r != parameters.r ||
               checkpoint.budget != parameters.budget ||
               checkpoint.tile_side != options->tile_side ||
               checkpoint.total_tiles != total_tiles ||
               checkpoint.value_bytes != estimate.value_bytes ||
               checkpoint.choice_bytes != estimate.choice_bytes ||
               checkpoint.next_tile > total_tiles) {
        return fail("checkpoint does not match this calculation");
    }
    int value_descriptor;
    int choice_descriptor;
    uint64_t *values;
    uint32_t *choices;

    // Map the shared file-backed arrays once for the lifetime of this field calculation.
    if (!map_state_file(
            value_path,
            (size_t)estimate.value_bytes,
            fresh,
            &value_descriptor,
            (void **)&values) ||
        !map_state_file(
            choice_path,
            (size_t)estimate.choice_bytes,
            fresh,
            &choice_descriptor,
            (void **)&choices)) {
        return fail("cannot create or map DP state files");
    }

    // Publish the initial cursor after both new mappings exist.
    if (fresh && !commit_checkpoint(
            values,
            (size_t)estimate.value_bytes,
            choices,
            (size_t)estimate.choice_bytes,
            &checkpoint,
            checkpoint_path,
            temporary_path)) {
        return fail("cannot write initial checkpoint");
    }
    kh_transition_table_t table;
    double build_started = elapsed_clock();

    // Keep raw construction and exact reduction encapsulated outside the DP engine.
    if (!kh_build_transitions(&parameters, !options->raw_transitions, &table, &error)) {
        return fail(error);
    }
    double build_seconds = elapsed_clock() - build_started;
    double compute_seconds = 0;
    double checkpoint_seconds = 0;
    kh_transition_t *transitions = table.entries;
    dp_context_t context = {
        .values = values,
        .choices = choices,
        .side = (size_t)estimate.side,
        .p = parameters.p,
        .transitions = transitions,
        .transition_count = table.count
    };

    // Create and pin workers once, sharing mappings and immutable transitions.
    kh_pool_t *pool = kh_pool_create(options->threads, evaluate_cell, &context, &error);

    if (pool == NULL) {
        return fail(error);
    }

    // Record actual placement before computation for diagnostics and benchmarks.
    fprintf(stderr, "DP workers=%u; CPUs:", options->threads);

    for (uint32_t index = 0; index < options->threads; ++index) {
        fprintf(stderr, " %d", kh_pool_cpu(pool, index));
    }
    fprintf(stderr, "\n");
    fflush(stderr);
    uint64_t completed_rows = (checkpoint.next_tile / tile_rows) * options->tile_side;

    // The final clipped tile row must not inflate the restart baseline.
    if (completed_rows > parameters.budget) {
        completed_rows = parameters.budget;
    }
    uint64_t current_height = parameters.budget - completed_rows;

    // Count a partial tile row using its actual height.
    if (current_height > options->tile_side) {
        current_height = options->tile_side;
    }
    uint64_t baseline = completed_rows * parameters.budget +
                        current_height * (checkpoint.next_tile % tile_rows) * options->tile_side;
    kh_progress_t *progress = kh_progress_start(
        pool,
        baseline,
        checkpoint.next_tile,
        estimate.active_cells,
        options->threads,
        options->progress_milliseconds,
        &error
    );

    // No observer may outlive the worker pool it reads.
    if (progress == NULL) {
        kh_pool_destroy(pool);
        return fail(error);
    }
    uint64_t last_checkpoint = monotonic_seconds();
    uint64_t invocation_tiles = 0;

    // Resume at the first tile not covered by durable metadata.
    for (uint64_t tile_index = checkpoint.next_tile;
         tile_index < total_tiles;
         ++tile_index) {
        uint64_t tile_row = tile_index / tile_rows;
        uint64_t tile_column = tile_index % tile_rows;
        uint32_t first_u = (uint32_t)(tile_row * options->tile_side + 1);
        uint32_t first_v = (uint32_t)(tile_column * options->tile_side + 1);
        uint32_t last_u = first_u + options->tile_side - 1;
        uint32_t last_v = first_v + options->tile_side - 1;

        // Clip edge tiles to the full standard budget.
        if (last_u > parameters.budget || last_u < first_u) {
            last_u = parameters.budget;
        }

        // Clip the symbol-budget edge independently.
        if (last_v > parameters.budget || last_v < first_v) {
            last_v = parameters.budget;
        }

        // The pool synchronizes each diagonal before dependent cells proceed.
        double compute_started = elapsed_clock();
        kh_pool_fill(pool, first_u, last_u, first_v, last_v);
        compute_seconds += elapsed_clock() - compute_started;
        checkpoint.next_tile = tile_index + 1;
        ++invocation_tiles;
        uint64_t now = monotonic_seconds();
        bool deadline = options->checkpoint_seconds == 0 ||
                        now - last_checkpoint >= options->checkpoint_seconds;
        bool test_stop = options->stop_after_tiles != 0 &&
                         invocation_tiles >= options->stop_after_tiles;
        bool final_tile = checkpoint.next_tile == total_tiles;

        // Commit at the configured interval and every intentional exit boundary.
        if (deadline || stop_requested || test_stop || final_tile) {
            kh_progress_phase(progress, "checkpointing");
            double checkpoint_started = elapsed_clock();
            if (!commit_checkpoint(
                    values,
                    (size_t)estimate.value_bytes,
                    choices,
                    (size_t)estimate.choice_bytes,
                    &checkpoint,
                    checkpoint_path,
                    temporary_path)) {
                kh_progress_stop(progress);
                kh_pool_destroy(pool);
                return fail("checkpoint commit failed");
            }
            checkpoint_seconds += elapsed_clock() - checkpoint_started;
            last_checkpoint = now;
            kh_progress_checkpoint(progress, checkpoint.next_tile);

            // Workers remain idle while the agent produces an immutable local snapshot.
            if (options->checkpoint_handshake) {
                kh_progress_phase(progress, "snapshotting");

                // A lost supervisor cannot leave the solver modifying uncaptured state.
                if (!snapshot_handshake(checkpoint.next_tile)) {
                    kh_progress_stop(progress);
                    kh_pool_destroy(pool);
                    return fail("checkpoint snapshot handshake failed");
                }
            }
            kh_progress_phase(progress, "computing");
        }

        // Exit only after the completed tile and cursor are durable.
        if (stop_requested || test_stop) {
            kh_progress_phase(progress, "stopped");
            kh_progress_stop(progress);
            kh_pool_destroy(pool);
            free(transitions);
            munmap(values, (size_t)estimate.value_bytes);
            munmap(choices, (size_t)estimate.choice_bytes);
            close(value_descriptor);
            close(choice_descriptor);
            return 75;
        }
    }
    kh_progress_phase(progress, "reconstructing");
    kh_progress_stop(progress);
    kh_pool_destroy(pool);
    uint32_t u = parameters.budget;
    uint32_t v = parameters.budget;
    output_step_t *steps = calloc((size_t)parameters.budget, sizeof *steps);
    uint32_t step_count = 0;

    // Reserve enough steps for the worst case of unit budget consumption.
    if (steps == NULL) {
        return fail("cannot allocate split reconstruction");
    }

    // Follow chosen predecessors from the full-budget optimum.
    while (choices[(size_t)u * (size_t)estimate.side + v] != 0) {
        uint32_t choice = choices[(size_t)u * (size_t)estimate.side + v];
        kh_transition_t transition;

        // Decode original IDs, including choices written by the unpruned solver.
        if (!kh_decode_transition(parameters.p, choice, &transition) ||
            step_count >= parameters.budget ||
            (uint32_t)transition.a * transition.t > u ||
            (uint32_t)transition.b * transition.t > v) {
            return fail("invalid choice during reconstruction");
        }
        steps[step_count].a = transition.a;
        steps[step_count].b = transition.b;
        steps[step_count].t = transition.t;
        ++step_count;
        u -= transition.a * transition.t;
        v -= transition.b * transition.t;
    }
    uint64_t theta = values[(size_t)parameters.budget * (size_t)estimate.side +
                            parameters.budget];

    // Publish the compact result only after successful reconstruction.
    if (!write_artifact(
            options->output_path,
            &parameters,
            theta,
            steps,
            step_count)) {
        return fail("cannot create output artifact");
    }
    fprintf(stderr,
            "DP optimum theta=%" PRIu64 "; steps=%" PRIu32 "; saved %s\n",
            theta,
            step_count,
            options->output_path);
    fprintf(stderr,
            "DP metrics={\"mode\":\"%s\",\"raw_transitions\":%u,"
            "\"scan_transitions\":%u,\"transition_array_bytes\":%" PRIu64 ","
            "\"transition_array_peak_bytes\":%" PRIu64 ","
            "\"build_seconds\":%.9f,\"compute_seconds\":%.9f,"
            "\"checkpoint_seconds\":%.9f}\n",
            options->raw_transitions ? "raw" : "same-cost",
            table.raw_count,
            table.count,
            table.array_bytes,
            table.array_peak_bytes,
            build_seconds,
            compute_seconds,
            checkpoint_seconds);
    free(steps);
    free(transitions);
    munmap(values, (size_t)estimate.value_bytes);
    munmap(choices, (size_t)estimate.choice_bytes);
    close(value_descriptor);
    close(choice_descriptor);
    return 0;
}

/*
 * Parse the CLI, install stop handlers, and execute the local DP.
 *
 * Parameters:
 *   argc: Number of command-line arguments.
 *   argv: Input argument vector.
 *
 * Returns:
 *   Zero for help or completion, 75 for a checkpointed stop, and one on error.
 */
int main(int argc, char **argv) {

    // Treat an empty invocation as a successful help request.
    if (argc == 1 || (argc == 2 && (!strcmp(argv[1], "--help") || !strcmp(argv[1], "-h")))) {
        help();
        return 0;
    }
    options_t options;

    // Reject incomplete or unrecognized syntax before modifying files.
    if (!parse_options(argc, argv, &options)) {
        help();
        return 1;
    }
    struct sigaction action = {0};
    action.sa_handler = request_stop;
    sigemptyset(&action.sa_mask);

    // Convert termination signals into consistent tile-boundary checkpoints.
    if (sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0) {
        return fail("cannot install signal handlers");
    }
    return run(&options);
}
