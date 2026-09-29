#define _GNU_SOURCE

#include "kh_checkpoint.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// One streaming checkpoint checksum, independent of host word endianness.
typedef struct {
    FILE *file;
    uint64_t hash;
} checkpoint_stream_t;

/*
 * Update the v1 FNV-1a checksum over exact bytes on disk.
 * Parameters: current: Existing hash; bytes: Input bytes; count: Number of bytes.
 * Returns: Updated 64-bit noncryptographic integrity checksum.
 */
static uint64_t checksum(uint64_t current, const unsigned char *bytes, size_t count) {

    // Hash byte order is fixed by explicit little-endian integer encoding.
    for (size_t index = 0; index < count; ++index) {
        current ^= bytes[index];
        current *= UINT64_C(1099511628211);
    }
    return current;
}

/*
 * Write and hash one exact byte string.
 * Parameters: stream: Writable checkpoint stream; bytes: Input payload; count: Length.
 * Returns: True after a complete write; false on I/O failure.
 */
static bool write_bytes(checkpoint_stream_t *stream, const void *bytes, size_t count) {

    // A partial write leaves only a disposable temporary file.
    if (fwrite(bytes, 1, count, stream->file) != count) {
        return false;
    }
    stream->hash = checksum(stream->hash, bytes, count);
    return true;
}

/*
 * Read and hash one exact byte string.
 * Parameters: stream: Readable checkpoint stream; bytes: Output buffer; count: Length.
 * Returns: True after complete read; false on truncation or I/O failure.
 */
static bool read_bytes(checkpoint_stream_t *stream, void *bytes, size_t count) {

    // Truncation must not produce a partially accepted matching.
    if (fread(bytes, 1, count, stream->file) != count) {
        return false;
    }
    stream->hash = checksum(stream->hash, bytes, count);
    return true;
}

/*
 * Encode one portable little-endian 32-bit integer.
 * Parameters: stream: Writable stream; value: Input value.
 * Returns: True on complete write; false on I/O failure.
 */
static bool write_u32(checkpoint_stream_t *stream, uint32_t value) {
    unsigned char bytes[4];

    // Native structure padding is never part of the checkpoint format.
    for (uint32_t index = 0; index < 4; ++index) {
        bytes[index] = (unsigned char)(value >> (index * 8));
    }
    return write_bytes(stream, bytes, sizeof bytes);
}

/*
 * Decode one portable little-endian 32-bit integer.
 * Parameters: stream: Readable stream; value: Output value.
 * Returns: True on complete read; false on truncation.
 */
static bool read_u32(checkpoint_stream_t *stream, uint32_t *value) {
    unsigned char bytes[4];

    // All four bytes must be present before the output is changed.
    if (!read_bytes(stream, bytes, sizeof bytes)) {
        return false;
    }
    *value = 0;

    // Reconstruct unsigned words without alignment assumptions.
    for (uint32_t index = 0; index < 4; ++index) {
        *value |= (uint32_t)bytes[index] << (index * 8);
    }
    return true;
}

/*
 * Encode one portable little-endian 64-bit integer.
 * Parameters: stream: Writable stream; value: Input value.
 * Returns: True on complete write; false on I/O failure.
 */
static bool write_u64(checkpoint_stream_t *stream, uint64_t value) {
    unsigned char bytes[8];

    // The format does not depend on the worker's native endianness.
    for (uint32_t index = 0; index < 8; ++index) {
        bytes[index] = (unsigned char)(value >> (index * 8));
    }
    return write_bytes(stream, bytes, sizeof bytes);
}

/*
 * Decode one portable little-endian 64-bit integer.
 * Parameters: stream: Readable stream; value: Output value.
 * Returns: True on complete read; false on truncation.
 */
static bool read_u64(checkpoint_stream_t *stream, uint64_t *value) {
    unsigned char bytes[8];

    // Truncated counters cannot be interpreted as a valid checkpoint cursor.
    if (!read_bytes(stream, bytes, sizeof bytes)) {
        return false;
    }
    *value = 0;

    // Combine exact bytes with shifts below the uint64 width.
    for (uint32_t index = 0; index < 8; ++index) {
        *value |= (uint64_t)bytes[index] << (index * 8);
    }
    return true;
}

/*
 * Validate a matching without running any augmenting-path search.
 * Parameters: graph: Borrowed field and requests; matching: Completed phase state; error: Static diagnostic.
 * Returns: True only for legal unique selected edges and the stated cardinality.
 */
static bool validate_state(const kh_graph_t *graph, const kh_matching_t *matching, const char **error) {
    uint64_t count = 0;

    // Every matched left endpoint must agree with its chosen implicit edge.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        uint32_t v = matching->left[u];

        // Unmatched vertices have no selected edge.
        if (v == UINT32_MAX) {
            continue;
        }
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);

        // Right ownership, choice range and field edge must all agree.
        if (v >= graph->field->parameters.q || matching->right[v] != u ||
            matching->choice[u] >= graph->field->parameters.f ||
            kh_neighbor(graph, coset, cell, matching->choice[u]) != v) {
            *error = "checkpoint matching contains an invalid edge";
            return false;
        }
        ++count;
    }

    // Cardinality and completed phase count bind the restart cursor.
    if (count != matching->matched || matching->phases == 0) {
        *error = "checkpoint matching count or phase is inconsistent";
        return false;
    }
    return true;
}

/*
 * Serialize the matching and its complete graph identity to one open stream.
 * Parameters: stream: Writable checksum stream; graph: Borrowed graph; matching: Valid phase state; dp_hash: Input DP hash.
 * Returns: True after writing the trailing checksum; false on I/O failure.
 */
static bool write_payload(checkpoint_stream_t *stream, const kh_graph_t *graph,
                          const kh_matching_t *matching, const unsigned char dp_hash[32]) {
    const kh_parameters_t *parameters = &graph->field->parameters;

    // The identity includes a version and the immutable input DP digest.
    if (!write_bytes(stream, "KHC1", 4) || !write_bytes(stream, dp_hash, 32) ||
        !write_u32(stream, parameters->p) || !write_u32(stream, parameters->r) ||
        !write_u32(stream, parameters->q) || !write_u32(stream, parameters->f) ||
        !write_u32(stream, graph->count) || !write_u32(stream, graph->block_count) ||
        !write_u32(stream, matching->matched) || !write_u64(stream, matching->phases) ||
        !write_u64(stream, matching->scans)) {
        return false;
    }

    // The fixed generator X is implicit, while reduction coefficients are explicit.
    for (uint32_t index = 0; index <= parameters->r; ++index) {
        if (!write_u32(stream, graph->field->polynomial[index])) {
            return false;
        }
    }

    // Compact blocks bind the exact canonical left-vertex enumeration.
    for (uint32_t index = 0; index < graph->block_count; ++index) {
        const kh_request_block_t *block = &graph->blocks[index];
        if (!write_u64(stream, block->first) || !write_u32(stream, block->coset) ||
            !write_u32(stream, block->copies) || !write_u32(stream, block->stripes)) {
            return false;
        }
    }

    // Store only left assignments and neighbor choices; right ownership is reconstructed.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        uint32_t choice = matching->left[u] == UINT32_MAX ? UINT32_MAX : matching->choice[u];
        if (!write_u32(stream, matching->left[u]) || !write_u32(stream, choice)) {
            return false;
        }
    }
    uint64_t digest = stream->hash;
    return write_u64(stream, digest);
}

/*
 * Sync the directory entry after atomically replacing one checkpoint path.
 * Parameters: path: Published checkpoint path.
 * Returns: True when its parent directory is synced; false on a filesystem error.
 */
static bool sync_parent(const char *path) {
    char *copy = strdup(path);

    // A path without a slash resides in the current directory.
    if (copy == NULL) {
        return false;
    }
    char *slash = strrchr(copy, '/');

    // Isolate the directory without modifying the caller's path.
    if (slash == NULL) {
        strcpy(copy, ".");
    } else if (slash == copy) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    int descriptor = open(copy, O_RDONLY | O_DIRECTORY);
    free(copy);

    // A durable rename includes the parent directory entry.
    if (descriptor < 0) {
        return false;
    }
    bool valid = fsync(descriptor) == 0;
    close(descriptor);
    return valid;
}

/*
 * Atomically publish one checked phase state without a torn destination file.
 * Parameters: path: Replaceable output; graph: Borrowed graph; matching: Valid phase state; dp_hash: Input identity; error: Diagnostic.
 * Returns: True after syncing; false with temporary file removed.
 */
bool kh_checkpoint_write(const char *path, const kh_graph_t *graph,
                         const kh_matching_t *matching, const unsigned char dp_hash[32],
                         const char **error) {

    // Refuse to persist a state whose selected edges or cardinality are inconsistent.
    if (!validate_state(graph, matching, error)) {
        return false;
    }
    size_t length = strlen(path);
    char *temporary = malloc(length + 12);

    // mkstemp owns an exclusive temporary beside the intended checkpoint.
    if (temporary == NULL) {
        *error = "cannot allocate checkpoint filename";
        return false;
    }
    snprintf(temporary, length + 12, "%s.tmp-XXXXXX", path);
    int descriptor = mkstemp(temporary);
    bool valid = descriptor >= 0;

    // A failed temporary create must not change the old checkpoint.
    if (!valid) {
        *error = "cannot create checkpoint temporary file";
        free(temporary);
        return false;
    }
    FILE *file = fdopen(descriptor, "wb");

    // Stream conversion failures still leave the descriptor owned here.
    if (file == NULL) {
        close(descriptor);
        unlink(temporary);
        free(temporary);
        *error = "cannot open checkpoint stream";
        return false;
    }
    checkpoint_stream_t stream = {file, UINT64_C(14695981039346656037)};
    valid = write_payload(&stream, graph, matching, dp_hash) && fflush(file) == 0 && fsync(fileno(file)) == 0;

    // Closing a failed stream precedes removing its temporary path.
    if (fclose(file) != 0) {
        valid = false;
    }

    // Rename only a completely flushed and synced checkpoint.
    if (valid) {
        valid = rename(temporary, path) == 0;
    }

    // An unsuccessful rename leaves the previous checkpoint intact.
    if (!valid) {
        unlink(temporary);
        *error = "cannot write durable matching checkpoint";
    } else if (!sync_parent(path)) {
        *error = "cannot sync matching checkpoint directory";
        valid = false;
    }
    free(temporary);
    return valid;
}

/*
 * Check exact graph identity and rebuild both pairing directions from selected edges.
 * Parameters: stream: Readable checksum stream; graph: Borrowed target graph; matching: Allocated empty output; dp_hash: Required DP hash.
 * Returns: True after payload and checksum validation; false on mismatch or truncation.
 */
static bool read_payload(checkpoint_stream_t *stream, const kh_graph_t *graph,
                         kh_matching_t *matching, const unsigned char dp_hash[32]) {
    unsigned char magic[4];
    unsigned char stored_hash[32];
    uint32_t header[7];
    uint64_t phases;
    uint64_t scans;
    const kh_parameters_t *parameters = &graph->field->parameters;

    // All identity fields must match before reading large matching arrays.
    if (!read_bytes(stream, magic, sizeof magic) || memcmp(magic, "KHC1", 4) != 0 ||
        !read_bytes(stream, stored_hash, sizeof stored_hash) || memcmp(stored_hash, dp_hash, 32) != 0) {
        return false;
    }

    // Fixed-width integer order is part of the checkpoint format.
    for (uint32_t index = 0; index < 7; ++index) {
        if (!read_u32(stream, &header[index])) {
            return false;
        }
    }
    if (!read_u64(stream, &phases) || !read_u64(stream, &scans) ||
        header[0] != parameters->p || header[1] != parameters->r ||
        header[2] != parameters->q || header[3] != parameters->f ||
        header[4] != graph->count || header[5] != graph->block_count ||
        header[6] > graph->count || phases == 0) {
        return false;
    }

    // A different primitive polynomial changes the entire implicit graph.
    for (uint32_t index = 0; index <= parameters->r; ++index) {
        uint32_t coefficient;
        if (!read_u32(stream, &coefficient) || coefficient != graph->field->polynomial[index]) {
            return false;
        }
    }

    // A matching can resume only with identical request ordering and cosets.
    for (uint32_t index = 0; index < graph->block_count; ++index) {
        uint64_t first;
        uint32_t coset;
        uint32_t copies;
        uint32_t stripes;
        const kh_request_block_t *block = &graph->blocks[index];
        if (!read_u64(stream, &first) || !read_u32(stream, &coset) ||
            !read_u32(stream, &copies) || !read_u32(stream, &stripes) ||
            first != block->first || coset != block->coset ||
            copies != block->copies || stripes != block->stripes) {
            return false;
        }
    }
    memset(matching->right, 255, (size_t)parameters->q * sizeof(uint32_t));
    uint32_t counted = 0;

    // Read one left assignment and its selected neighbor index at a time.
    for (uint64_t index = 0; index < graph->count; ++index) {
        uint32_t u = (uint32_t)index;
        uint32_t v;
        uint32_t choice;

        // An unmatched request must use the explicit absent-choice sentinel.
        if (!read_u32(stream, &v) || !read_u32(stream, &choice)) {
            return false;
        }
        matching->left[u] = v;
        matching->choice[u] = choice;

        // Unmatched requests have no right owner to rebuild.
        if (v == UINT32_MAX) {
            if (choice != UINT32_MAX) {
                return false;
            }
            continue;
        }
        uint32_t coset;
        uint32_t cell;
        kh_request(graph, u, &coset, &cell);

        // Reject repeated rights and edges outside the implicit graph.
        if (v >= parameters->q || choice >= parameters->f ||
            matching->right[v] != UINT32_MAX || kh_neighbor(graph, coset, cell, choice) != v) {
            return false;
        }
        matching->right[v] = u;
        ++counted;
    }

    // Cardinality is independently recomputed before the cursor is accepted.
    if (counted != header[6]) {
        return false;
    }
    uint64_t expected = stream->hash;
    uint64_t recorded;

    // The checksum itself is read without including its own bytes in the digest.
    if (!read_u64(stream, &recorded) || recorded != expected) {
        return false;
    }
    matching->matched = counted;
    matching->phases = phases;
    matching->scans = scans;
    return true;
}

/*
 * Restore one exact checkpoint with length, checksum and edge validation.
 * Parameters: path: Input file; graph: Target graph; matching: Allocated empty state; dp_hash: Required DP hash; error: Diagnostic.
 * Returns: True for a complete compatible matching phase; false otherwise.
 */
bool kh_checkpoint_read(const char *path, const kh_graph_t *graph,
                        kh_matching_t *matching, const unsigned char dp_hash[32],
                        const char **error) {
    FILE *file = fopen(path, "rb");

    // A missing checkpoint is not silently interpreted as a fresh run.
    if (file == NULL) {
        *error = "cannot open matching checkpoint";
        return false;
    }
    struct stat information;
    uint64_t expected = UINT64_C(88) + 4 * ((uint64_t)graph->field->parameters.r + 1) +
                        20 * graph->block_count + 8 * graph->count;
    bool valid = fstat(fileno(file), &information) == 0 && information.st_size >= 0 &&
                 (uint64_t)information.st_size == expected;

    // Length checks prevent extra bytes and truncated payloads from entering the decoder.
    if (valid) {
        checkpoint_stream_t stream = {file, UINT64_C(14695981039346656037)};
        valid = read_payload(&stream, graph, matching, dp_hash);
    }
    fclose(file);

    // The caller can fall back to another retained snapshot after any rejection.
    if (!valid) {
        *error = "matching checkpoint failed length, identity, edge or checksum validation";
    }
    return valid;
}
