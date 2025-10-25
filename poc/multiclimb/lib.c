#include "lib.h"
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <stdint.h>
#include <stddef.h>


typedef struct { int8_t *p; ssize_t off, len, ps; } task_t;

static void *touch_range(void *arg) {
    task_t *t = (task_t*)arg;
    for (ssize_t i = 0; i < t->len; i += t->ps) t->p[t->off + i] = 0;
    if (t->len) t->p[t->off + t->len - 1] = 0;
    return NULL;
}

void prefault_parallel(void *p, ssize_t bytes, int nthr) {
    ssize_t ps = (ssize_t)sysconf(_SC_PAGESIZE);
    pthread_t th[nthr];
    task_t    td[nthr];
    ssize_t chunk = (bytes + nthr - 1) / nthr;
    for (int t = 0; t < nthr; ++t) {
        ssize_t off = (ssize_t)t * chunk;
        ssize_t len = off >= bytes ? 0 : (bytes - off < chunk ? bytes - off : chunk);
        td[t] = (task_t){ .p = (int8_t*)p, .off = off, .len = len, .ps = ps };
        pthread_create(&th[t], NULL, touch_range, &td[t]);
    }
    for (int t = 0; t < nthr; ++t) pthread_join(th[t], NULL);
}

void prefault(void *p, ssize_t bytes) {
    ssize_t ps = (ssize_t)sysconf(_SC_PAGESIZE);
    volatile int8_t *q = (int8_t*)p;
    for (ssize_t i = 0; i < bytes; i += ps) q[i] = 0;  // write = faults page in
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

void* zmalloc(ssize_t bytes) {
    void* buf = mmap(
        NULL,                 // let kernel choose the address
        bytes,                // length of the mapping in bytes
        PROT_READ | PROT_WRITE,       // readable + writable
        MAP_SHARED | MAP_ANONYMOUS,   // visible to children after fork; not backed by a file
        -1,                   // fd unused with MAP_ANONYMOUS
        0                     // offset
    );
    madvise(buf, bytes, MADV_WILLNEED);
    #ifdef MADV_HUGEPAGE
    madvise(buf, bytes, MADV_HUGEPAGE);
    #endif

    prefault(buf, bytes);
    return buf;
}

void* zcalloc(ssize_t num_elements, ssize_t num_bytes) {
    return zmalloc(num_elements * num_bytes);
}

#if HAVE_CROARING == 1

// Function to get the size of a bit array
bitlut_t* make_bitset(ssize_t num_bits) {
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
bool bit_get(const bitlut_t *bit_array, long long bit_index) {
    return roaring64_bitmap_contains(bit_array, bit_index);
}

ssize_t bit_sum(const bitlut_t *buf, ssize_t nbits){
    (void) nbits;
    return roaring64_bitmap_get_cardinality(buf);
}

void bitmap_free(bitlut_t *bit_array) {
    roaring64_bitmap_free(bit_array);
}

#else

// Function to get the size of a bit array
bitlut_t* make_bitset(ssize_t num_bits) {
    ssize_t bytes = 1 + (num_bits >> 3);
    return (bitlut_t*) zmalloc(bytes);
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
bool bit_get(const bitlut_t *bit_array, long long bit_index) {
    long long byte_index = bit_index >> 3;
    long long bit_offset = bit_index & 7;
    return (bit_array[byte_index] >> bit_offset) & 1;
}

// ssize_t bit_sum(const bitlut_t *buf, ssize_t num_bits) {
//     ssize_t nbytes = 1 + (num_bits >> 3);
//     ssize_t sum = 0;
//     while(nbytes-->0){
//         bitlut_t c = *buf;
//         while (c) {
//             sum += c&1;
//             c>>=1;
//         }
//         buf++;
//     }
//     return sum;
// }

ssize_t bit_sum(const bitlut_t *buf, ssize_t num_bits) {
    ssize_t nbytes = 1 + (num_bits >> 3);
    ssize_t sum = 0;
    ssize_t i = 0;
    __m256i total = _mm256_setzero_si256();

    // Process 32 bytes (256 bits) per loop
    for (; i + 31 < nbytes; i += 32) {
        __m256i x = _mm256_loadu_si256((const __m256i*)(buf + i));
        total = _mm256_add_epi64(total, _mm256_sad_epu8(_mm256_popcnt_epi8(x), _mm256_setzero_si256()));
        // (requires AVX512 VPOPCNTDQ or use lookup table for AVX2-only)
    }
    
    // Extract partial sums
    int64_t tmp[4];
    _mm256_storeu_si256((__m256i*)tmp, total);
    sum = tmp[0] + tmp[1] + tmp[2] + tmp[3];

    // Finish remainder
    for (; i < nbytes; i++) sum += __builtin_popcount(buf[i]);
    return sum;
}

void bitmap_free(bitlut_t *bit_array, long long num_bits) {
    ssize_t nbytes = 1 + (num_bits >> 3);
    munmap(bit_array, nbytes);
}

void zfree(void* p, long long bytes) {
    munmap(p, bytes);
}

#endif
