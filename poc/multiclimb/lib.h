#ifndef SUDBOROUGH_LIB_H
#define SUDBOROUGH_LIB_H

#include <stddef.h>
#include <stdlib.h>
#include <stdbool.h>


#if __has_include(<roaring/roaring64.h>)
#  include <roaring/roaring64.h>
#  define HAVE_CROARING 0
#else
#  define HAVE_CROARING 0
#endif

#if HAVE_CROARING == 1
typedef roaring64_bitmap_t bitlut_t;
#else
typedef unsigned char bitlut_t;
#endif


void* zmalloc(size_t num_bytes);
void* zcalloc(size_t num_elements, size_t num_bytes);

bitlut_t* make_bitset(size_t num_bits);

// Function to set a bit in a bit array
void bit_set(bitlut_t *bit_array, long long bit_index);

// Function to clear a bit in a bit array
void bit_clear(bitlut_t *bit_array, long long bit_index);

// Function to check a bit in a bit array
bool bit_get(const bitlut_t *bit_array, long long bit_index);

size_t bit_sum(const bitlut_t *buf, size_t nbits);

void bitmap_free(bitlut_t *bit_array, long long num_bits);
void zfree(void* p, long long bytes);

long long nCr(long long n, long long k);

int next_combination(int *comb, int n, int k);

#endif