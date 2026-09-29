#include "kh_sha256.h"

#include <string.h>

static const uint32_t constants[64] = {
    UINT32_C(0x428a2f98), UINT32_C(0x71374491), UINT32_C(0xb5c0fbcf), UINT32_C(0xe9b5dba5),
    UINT32_C(0x3956c25b), UINT32_C(0x59f111f1), UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5),
    UINT32_C(0xd807aa98), UINT32_C(0x12835b01), UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
    UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe), UINT32_C(0x9bdc06a7), UINT32_C(0xc19bf174),
    UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786), UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc),
    UINT32_C(0x2de92c6f), UINT32_C(0x4a7484aa), UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
    UINT32_C(0x983e5152), UINT32_C(0xa831c66d), UINT32_C(0xb00327c8), UINT32_C(0xbf597fc7),
    UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147), UINT32_C(0x06ca6351), UINT32_C(0x14292967),
    UINT32_C(0x27b70a85), UINT32_C(0x2e1b2138), UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
    UINT32_C(0x650a7354), UINT32_C(0x766a0abb), UINT32_C(0x81c2c92e), UINT32_C(0x92722c85),
    UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b), UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3),
    UINT32_C(0xd192e819), UINT32_C(0xd6990624), UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
    UINT32_C(0x19a4c116), UINT32_C(0x1e376c08), UINT32_C(0x2748774c), UINT32_C(0x34b0bcb5),
    UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a), UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3),
    UINT32_C(0x748f82ee), UINT32_C(0x78a5636f), UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
    UINT32_C(0x90befffa), UINT32_C(0xa4506ceb), UINT32_C(0xbef9a3f7), UINT32_C(0xc67178f2),
};

static uint32_t rotate(uint32_t value, uint32_t bits) {
    return value >> bits | value << (32 - bits);
}

static void transform(kh_sha256_t *context, const unsigned char block[64]) {
    uint32_t words[64];
    for (uint32_t index = 0; index < 16; ++index) {
        words[index] = (uint32_t)block[4 * index] << 24 |
                       (uint32_t)block[4 * index + 1] << 16 |
                       (uint32_t)block[4 * index + 2] << 8 |
                       (uint32_t)block[4 * index + 3];
    }
    for (uint32_t index = 16; index < 64; ++index) {
        uint32_t a = words[index - 15];
        uint32_t b = words[index - 2];
        uint32_t s0 = rotate(a, 7) ^ rotate(a, 18) ^ (a >> 3);
        uint32_t s1 = rotate(b, 17) ^ rotate(b, 19) ^ (b >> 10);
        words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }
    uint32_t a = context->state[0];
    uint32_t b = context->state[1];
    uint32_t c = context->state[2];
    uint32_t d = context->state[3];
    uint32_t e = context->state[4];
    uint32_t f = context->state[5];
    uint32_t g = context->state[6];
    uint32_t h = context->state[7];
    for (uint32_t index = 0; index < 64; ++index) {
        uint32_t s1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
        uint32_t choice = (e & f) ^ (~e & g);
        uint32_t first = h + s1 + choice + constants[index] + words[index];
        uint32_t s0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t second = s0 + majority;
        h = g;
        g = f;
        f = e;
        e = d + first;
        d = c;
        c = b;
        b = a;
        a = first + second;
    }
    context->state[0] += a;
    context->state[1] += b;
    context->state[2] += c;
    context->state[3] += d;
    context->state[4] += e;
    context->state[5] += f;
    context->state[6] += g;
    context->state[7] += h;
}

void kh_sha256_init(kh_sha256_t *context) {
    static const uint32_t initial[8] = {
        UINT32_C(0x6a09e667), UINT32_C(0xbb67ae85), UINT32_C(0x3c6ef372), UINT32_C(0xa54ff53a),
        UINT32_C(0x510e527f), UINT32_C(0x9b05688c), UINT32_C(0x1f83d9ab), UINT32_C(0x5be0cd19),
    };
    memcpy(context->state, initial, sizeof initial);
    context->bytes = 0;
    context->used = 0;
}

void kh_sha256_update(kh_sha256_t *context, const void *input, size_t count) {
    const unsigned char *bytes = input;
    context->bytes += count;
    while (count != 0) {
        size_t available = 64 - context->used;
        size_t take = count < available ? count : available;
        memcpy(context->block + context->used, bytes, take);
        context->used += take;
        bytes += take;
        count -= take;
        if (context->used == 64) {
            transform(context, context->block);
            context->used = 0;
        }
    }
}

void kh_sha256_final(kh_sha256_t *context, unsigned char output[32]) {
    uint64_t bits = context->bytes * 8;
    context->block[context->used++] = 0x80;
    if (context->used > 56) {
        memset(context->block + context->used, 0, 64 - context->used);
        transform(context, context->block);
        context->used = 0;
    }
    memset(context->block + context->used, 0, 56 - context->used);
    for (uint32_t index = 0; index < 8; ++index) {
        context->block[63 - index] = (unsigned char)(bits >> (8 * index));
    }
    transform(context, context->block);
    for (uint32_t index = 0; index < 8; ++index) {
        output[4 * index] = (unsigned char)(context->state[index] >> 24);
        output[4 * index + 1] = (unsigned char)(context->state[index] >> 16);
        output[4 * index + 2] = (unsigned char)(context->state[index] >> 8);
        output[4 * index + 3] = (unsigned char)context->state[index];
    }
    memset(context, 0, sizeof *context);
}
