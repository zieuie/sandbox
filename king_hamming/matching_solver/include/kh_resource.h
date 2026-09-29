#ifndef KH_RESOURCE_H
#define KH_RESOURCE_H

#include <stdbool.h>
#include <stdint.h>

/* Read lifetime CPU time and peak resident memory for the calling process. */
bool kh_resource_snapshot(uint64_t *cpu_microseconds, uint64_t *peak_rss_bytes);

/* Emit one compact cluster-consumable JSON record for the calling process. */
bool kh_resource_print(const char *component, int32_t shard_index);

#endif
