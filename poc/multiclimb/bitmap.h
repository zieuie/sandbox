#ifndef BITMAP_H
#define BITMAP_H

// Function to set a bit in a bit array
void bit_set(unsigned char *bit_array, int bit_index);

// Function to clear a bit in a bit array
void bit_clear(unsigned char *bit_array, int bit_index);

// Function to check a bit in a bit array
int bit_get(unsigned char *bit_array, int bit_index);

#endif