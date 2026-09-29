#include "kh_resource.h"

#include <inttypes.h>
#include <stdio.h>
#include <sys/resource.h>

bool kh_resource_snapshot(uint64_t *cpu_microseconds, uint64_t *peak_rss_bytes) {
    struct rusage usage;
    if (cpu_microseconds == NULL || peak_rss_bytes == NULL ||
        getrusage(RUSAGE_SELF, &usage) != 0) {
        return false;
    }
    uint64_t seconds = (uint64_t)usage.ru_utime.tv_sec + (uint64_t)usage.ru_stime.tv_sec;
    uint64_t microseconds = (uint64_t)usage.ru_utime.tv_usec +
                            (uint64_t)usage.ru_stime.tv_usec;
    *cpu_microseconds = seconds * UINT64_C(1000000) + microseconds;

    /* Linux reports ru_maxrss in KiB. The native engine is Linux-only because
       its thread and process placement uses sched_setaffinity. */
    *peak_rss_bytes = usage.ru_maxrss > 0 ? (uint64_t)usage.ru_maxrss * UINT64_C(1024) : 0;
    return true;
}

bool kh_resource_print(const char *component, int32_t shard_index) {
    uint64_t cpu_microseconds;
    uint64_t peak_rss_bytes;
    if (component == NULL || !kh_resource_snapshot(&cpu_microseconds, &peak_rss_bytes)) {
        return false;
    }
    printf("{\"event\":\"resource_usage\",\"component\":\"%s\","
           "\"shard_index\":%" PRId32 ",\"cpu_microseconds\":%" PRIu64
           ",\"peak_rss_bytes\":%" PRIu64 "}\n",
           component, shard_index, cpu_microseconds, peak_rss_bytes);
    fflush(stdout);
    return true;
}
