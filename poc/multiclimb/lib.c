#include "lib.h"
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <stdint.h>
#include <roaring/roaring64.h>

typedef struct { uint8_t *p; size_t off, len, ps; } task_t;

static void *touch_range(void *arg) {
    task_t *t = (task_t*)arg;
    for (size_t i = 0; i < t->len; i += t->ps) t->p[t->off + i] = 0;
    if (t->len) t->p[t->off + t->len - 1] = 0;
    return NULL;
}

void prefault_parallel(void *p, size_t bytes, int nthr) {
    size_t ps = (size_t)sysconf(_SC_PAGESIZE);
    pthread_t th[nthr];
    task_t    td[nthr];
    size_t chunk = (bytes + nthr - 1) / nthr;
    for (int t = 0; t < nthr; ++t) {
        size_t off = (size_t)t * chunk;
        size_t len = off >= bytes ? 0 : (bytes - off < chunk ? bytes - off : chunk);
        td[t] = (task_t){ .p = (uint8_t*)p, .off = off, .len = len, .ps = ps };
        pthread_create(&th[t], NULL, touch_range, &td[t]);
    }
    for (int t = 0; t < nthr; ++t) pthread_join(th[t], NULL);
}

void prefault(void *p, size_t bytes) {
    size_t ps = (size_t)sysconf(_SC_PAGESIZE);
    volatile uint8_t *q = (uint8_t*)p;
    for (size_t i = 0; i < bytes; i += ps) q[i] = 0;  // write = faults page in
    if (bytes) q[bytes-1] = 0;                        // touch last page
}

long long nCr(long long n, long long k) {
    long long ret = 1;
    k = k > n - k ? n - k : k;
    for (int j = 1; j <= k; j++) {
        ret = ret * (n - j + 1) / j;
    }
    return ret;
}

// returns true if there's another combination
int next_combination(int *comb, int n, int k) {
    int i = k - 1;
    while (i >= 0 && comb[i] == n - k + i) {
        i--;
    }
    if (i < 0) {
        return 0;
    }
    comb[i]++;
    for (int j = i + 1; j < k; j++) {
        comb[j] = comb[j - 1] + 1;
    }
    return 1;
}


#ifdef HAVE_CROARING

// Function to get the size of a bit array
bitlut_t* make_bitset(size_t num_bits) {
    (void) num_bits;
    return roaring64_bitmap_create();
}

// Function to set a bit in a bit array
void bit_set(bitlut_t *bit_array, long long bit_index) {
    roaring64_bitmap_add(bit_array, bit_index);
}

// Function to clear a bit in a bit array
void bit_clear(bitlut_t *bit_array, long long bit_index) {
    roaring64_bitmap_remove(bit_array, bit_index);
}

// Function to check a bit in a bit array
bool bit_get(bitlut_t *bit_array, long long bit_index) {
    return roaring64_bitmap_contains(bit_array, bit_index);
}

void bitmap_free(bitlut_t *bit_array) {
    roaring64_bitmap_free(bit_array);
}

#else

// Function to get the size of a bit array
bitlut_t* make_bitset(size_t num_bits) {
    size_t bytes = 1 + (num_bits >> 3);
    printf("%lu bits is %lu bytes\n", num_bits, bytes);
    fflush(stdout);

    void* buf = calloc(bytes, sizeof(bitlut_t));

    // void *buf = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                    //  MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    // if (buf == MAP_FAILED) { perror("mmap failed"); exit(1); }

    // madvise(buf, bytes, MADV_HUGEPAGE);
    // prefault_parallel(buf, bytes, /*nthr=*/(int)sysconf(_SC_NPROCESSORS_ONLN));
    prefault(buf, bytes);
    return (bitlut_t*) buf;
}

// Function to set a bit in a bit array
void bit_set(bitlut_t *bit_array, long long bit_index) {
    long long byte_index = bit_index >> 3;
    long long bit_offset = bit_index & 7;
    bit_array[byte_index] |= (1 << bit_offset);
}

// Function to clear a bit in a bit array
void bit_clear(bitlut_t *bit_array, long long bit_index) {
    long long byte_index = bit_index >> 3;
    long long bit_offset = bit_index & 7;
    bit_array[byte_index] &= ~(1 << bit_offset);
}

// Function to check a bit in a bit array
bool bit_get(bitlut_t *bit_array, long long bit_index) {
    long long byte_index = bit_index >> 3;
    long long bit_offset = bit_index & 7;
    return (bit_array[byte_index] >> bit_offset) & 1;
}

void bitmap_free(bitlut_t *bit_array) {
    free(bit_array);
}

#endif
