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