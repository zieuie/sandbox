#ifndef KH_WIRE_H
#define KH_WIRE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    KH_WIRE_INIT = 1,
    KH_WIRE_SCAN = 2,
    KH_WIRE_STOP = 3,
    KH_WIRE_LEVEL = 4,
    KH_WIRE_PROPOSE = 5,
    KH_WIRE_APPLY = 6,
    KH_WIRE_HALL = 7,
};

typedef struct {
    uint32_t operation;
    uint64_t count;
} kh_wire_header_t;

/* Read or write one exact byte range, retrying interrupted system calls. */
bool kh_wire_read(int descriptor, void *output, size_t count);
bool kh_wire_write(int descriptor, const void *input, size_t count);

/* Portable little-endian scalar helpers used by the native worker protocol. */
bool kh_wire_read_u32(int descriptor, uint32_t *output);
bool kh_wire_read_u64(int descriptor, uint64_t *output);
bool kh_wire_write_u32(int descriptor, uint32_t value);
bool kh_wire_write_u64(int descriptor, uint64_t value);
bool kh_wire_read_u32s(int descriptor, uint32_t *output, size_t count);
bool kh_wire_write_u32s(int descriptor, const uint32_t *input, size_t count);

/* KHW1 requests and KHR1 responses use the same fixed 16-byte envelope. */
bool kh_wire_read_request(int descriptor, kh_wire_header_t *header);
bool kh_wire_write_request(int descriptor, uint32_t operation, uint64_t count);
bool kh_wire_read_response(int descriptor, uint32_t *status, uint64_t *count);
bool kh_wire_write_response(int descriptor, uint32_t status, uint64_t count);

#endif
