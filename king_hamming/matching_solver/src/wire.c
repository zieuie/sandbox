#include "kh_wire.h"

#include <errno.h>
#include <unistd.h>

bool kh_wire_read(int descriptor, void *output, size_t count) {
    unsigned char *bytes = output;
    while (count != 0) {
        ssize_t received = read(descriptor, bytes, count);
        if (received < 0 && errno == EINTR) {
            continue;
        }
        if (received <= 0) {
            return false;
        }
        bytes += (size_t)received;
        count -= (size_t)received;
    }
    return true;
}

bool kh_wire_write(int descriptor, const void *input, size_t count) {
    const unsigned char *bytes = input;
    while (count != 0) {
        ssize_t sent = write(descriptor, bytes, count);
        if (sent < 0 && errno == EINTR) {
            continue;
        }
        if (sent <= 0) {
            return false;
        }
        bytes += (size_t)sent;
        count -= (size_t)sent;
    }
    return true;
}

bool kh_wire_read_u32(int descriptor, uint32_t *output) {
    unsigned char bytes[4];
    if (!kh_wire_read(descriptor, bytes, sizeof bytes)) {
        return false;
    }
    *output = (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
              (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
    return true;
}

bool kh_wire_read_u64(int descriptor, uint64_t *output) {
    unsigned char bytes[8];
    if (!kh_wire_read(descriptor, bytes, sizeof bytes)) {
        return false;
    }
    uint64_t value = 0;
    for (uint32_t index = 0; index < 8; ++index) {
        value |= (uint64_t)bytes[index] << (8 * index);
    }
    *output = value;
    return true;
}

bool kh_wire_write_u32(int descriptor, uint32_t value) {
    unsigned char bytes[4];
    for (uint32_t index = 0; index < 4; ++index) {
        bytes[index] = (unsigned char)(value >> (8 * index));
    }
    return kh_wire_write(descriptor, bytes, sizeof bytes);
}

bool kh_wire_write_u64(int descriptor, uint64_t value) {
    unsigned char bytes[8];
    for (uint32_t index = 0; index < 8; ++index) {
        bytes[index] = (unsigned char)(value >> (8 * index));
    }
    return kh_wire_write(descriptor, bytes, sizeof bytes);
}

static bool read_magic(int descriptor, const char expected[4]) {
    char magic[4];
    return kh_wire_read(descriptor, magic, sizeof magic) &&
           magic[0] == expected[0] && magic[1] == expected[1] &&
           magic[2] == expected[2] && magic[3] == expected[3];
}

bool kh_wire_read_request(int descriptor, kh_wire_header_t *header) {
    return read_magic(descriptor, "KHW1") &&
           kh_wire_read_u32(descriptor, &header->operation) &&
           kh_wire_read_u64(descriptor, &header->count);
}

bool kh_wire_write_request(int descriptor, uint32_t operation, uint64_t count) {
    return kh_wire_write(descriptor, "KHW1", 4) &&
           kh_wire_write_u32(descriptor, operation) &&
           kh_wire_write_u64(descriptor, count);
}

bool kh_wire_read_response(int descriptor, uint32_t *status, uint64_t *count) {
    return read_magic(descriptor, "KHR1") &&
           kh_wire_read_u32(descriptor, status) &&
           kh_wire_read_u64(descriptor, count);
}

bool kh_wire_write_response(int descriptor, uint32_t status, uint64_t count) {
    return kh_wire_write(descriptor, "KHR1", 4) &&
           kh_wire_write_u32(descriptor, status) &&
           kh_wire_write_u64(descriptor, count);
}
