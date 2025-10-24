// Function to set a bit in a bit array
void bit_set(unsigned char *bit_array, int bit_index) {
    int byte_index = bit_index / 8;
    int bit_offset = bit_index % 8;
    bit_array[byte_index] |= (1 << bit_offset);
}

// Function to clear a bit in a bit array
void bit_clear(unsigned char *bit_array, int bit_index) {
    int byte_index = bit_index / 8;
    int bit_offset = bit_index % 8;
    bit_array[byte_index] &= ~(1 << bit_offset);
}

// Function to check a bit in a bit array
int bit_get(unsigned char *bit_array, int bit_index) {
    int byte_index = bit_index / 8;
    int bit_offset = bit_index % 8;
    return (bit_array[byte_index] >> bit_offset) & 1;
}
