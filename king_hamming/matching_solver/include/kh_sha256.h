#ifndef KH_SHA256_H
#define KH_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t bytes;
    unsigned char block[64];
    size_t used;
} kh_sha256_t;

void kh_sha256_init(kh_sha256_t *context);
void kh_sha256_update(kh_sha256_t *context, const void *input, size_t count);
void kh_sha256_final(kh_sha256_t *context, unsigned char output[32]);

#endif
