/* Link-only instrumentation for unchanged baseline sources. */
#include <inttypes.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/types.h>
#include <unistd.h>

static _Atomic uint64_t received_bytes, sent_bytes;
ssize_t __real_read(int, void *, size_t);
ssize_t __real_write(int, const void *, size_t);
ssize_t __wrap_read(int fd, void *data, size_t size) {
    ssize_t n=__real_read(fd,data,size);
    if(n>0) atomic_fetch_add(&received_bytes,(uint64_t)n);
    return n;
}
ssize_t __wrap_write(int fd, const void *data, size_t size) {
    ssize_t n=__real_write(fd,data,size);
    if(n>0) atomic_fetch_add(&sent_bytes,(uint64_t)n);
    return n;
}
__attribute__((destructor)) static void report(void) {
    fprintf(stderr,"{\"event\":\"wire_meter\",\"received_bytes\":%" PRIu64
            ",\"sent_bytes\":%" PRIu64 "}\n",received_bytes,sent_bytes);
}
