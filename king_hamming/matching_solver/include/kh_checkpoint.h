#ifndef KH_CHECKPOINT_H
#define KH_CHECKPOINT_H

#include "kh_matching.h"

/*
 * Atomically save a consistent phase-boundary matching checkpoint.
 * Parameters: path: Replaceable checkpoint path; graph: Borrowed graph; matching: Completed phase; dp_hash: 32-byte DP identity; error: Static diagnostic on failure.
 * Returns: True after file and directory sync; false with the previous checkpoint intact.
 */
bool kh_checkpoint_write(const char *path, const kh_graph_t *graph,
                         const kh_matching_t *matching, const unsigned char dp_hash[32],
                         const char **error);

/*
 * Restore and validate a complete phase-boundary matching checkpoint.
 * Parameters: path: Input checkpoint; graph: Same field and requests; matching: Allocated empty output state; dp_hash: Required DP identity; error: Static diagnostic on failure.
 * Returns: True after validating checksum, graph identity, every selected edge and right uniqueness; false on mismatch.
 */
bool kh_checkpoint_read(const char *path, const kh_graph_t *graph,
                        kh_matching_t *matching, const unsigned char dp_hash[32],
                        const char **error);

#endif
