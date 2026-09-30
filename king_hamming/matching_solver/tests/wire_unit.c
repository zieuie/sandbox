#include "kh_wire.h"

#include <stdint.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void) {
    int descriptors[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, descriptors) != 0) {
        return 1;
    }
    uint32_t input[257];
    uint32_t output[257] = {0};
    for (size_t index = 0; index < 257; ++index) {
        input[index] = (uint32_t)(index * UINT32_C(0x1020304) + 7);
    }
    if (!kh_wire_write_u32s(descriptors[0], input, 257) ||
        !kh_wire_read_u32s(descriptors[1], output, 257)) {
        return 1;
    }
    for (size_t index = 0; index < 257; ++index) {
        if (input[index] != output[index]) {
            return 1;
        }
    }

    const unsigned char little_endian[4] = {0x78, 0x56, 0x34, 0x12};
    uint32_t value = 0;
    for (size_t index = 0; index < sizeof little_endian; ++index) {
        if (write(descriptors[0], little_endian + index, 1) != 1) {
            return 1;
        }
    }
    if (!kh_wire_read_u32s(descriptors[1], &value, 1) ||
        value != UINT32_C(0x12345678)) {
        return 1;
    }
    close(descriptors[0]);
    close(descriptors[1]);
    puts("wire unit ok");
    return 0;
}
