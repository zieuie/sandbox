#ifndef SUDBOROUGH_LIB_H
#define SUDBOROUGH_LIB_H

#include <stddef.h>
#include <stdlib.h>
#include <stdbool.h>

typedef unsigned char bitset_t;

bitset_t* make_bitset(size_t num_bits);

// Function to set a bit in a bit array
void bit_set(unsigned char *bit_array, long long bit_index);

// Function to clear a bit in a bit array
void bit_clear(unsigned char *bit_array, long long bit_index);

// Function to check a bit in a bit array
bool bit_get(unsigned char *bit_array, long long bit_index);

long long nCr(long long n, long long k);

int next_combination(int *comb, int n, int k);

#endif