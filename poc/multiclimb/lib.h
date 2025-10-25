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

#ifdef HAVE_CROARING
typedef roaring64_bitmap_t bitlut_t;
#else
typedef unsigned char bitlut_t;
#endif

bitlut_t* make_bitset(size_t num_bits);

// Function to set a bit in a bit array
void bit_set(bitlut_t *bit_array, long long bit_index);

// Function to clear a bit in a bit array
void bit_clear(bitlut_t *bit_array, long long bit_index);

// Function to check a bit in a bit array
bool bit_get(bitlut_t *bit_array, long long bit_index);

void bitmap_free(bitlut_t *bit_array);


long long nCr(long long n, long long k);

int next_combination(int *comb, int n, int k);

#endif