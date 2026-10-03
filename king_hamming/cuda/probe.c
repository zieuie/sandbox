#include "kh_cuda.h"

#include <stdio.h>
#include <string.h>

// List usable CUDA devices as JSON lines; exit 1 when the driver is unusable.
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--help") == 0) {
        puts("List CUDA devices via the driver API as JSON lines.\nUsage: kh_cuda_probe\nExit 1 if no usable driver.");
        return 0;
    }
    return kh_cuda_probe();
}
