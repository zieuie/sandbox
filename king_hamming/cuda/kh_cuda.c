#define _GNU_SOURCE

#include "kh_cuda.h"

#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

#define ATTRIBUTE_MAJOR 75
#define ATTRIBUTE_MINOR 76

// Resolve one versioned driver symbol, preferring the _v2 ABI where it exists.
static void *symbol(void *library, const char *name, bool versioned) {
    void *result = NULL;
    if (versioned) {
        char wide[96];
        snprintf(wide, sizeof wide, "%s_v2", name);
        result = dlsym(library, wide);
    }
    return result != NULL ? result : dlsym(library, name);
}

static bool load(kh_cuda_t *cuda, const char **error) {
    cuda->library = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
    if (cuda->library == NULL) {
        *error = "NVIDIA driver library libcuda.so.1 is not available";
        return false;
    }
#define BIND(field, versioned) \
    *(void **)&cuda->field = symbol(cuda->library, #field, versioned); \
    if (cuda->field == NULL) { *error = "driver lacks " #field; return false; }
    BIND(cuInit, false)
    BIND(cuDeviceGet, false)
    BIND(cuDeviceGetCount, false)
    BIND(cuDeviceGetName, false)
    BIND(cuDeviceGetAttribute, false)
    BIND(cuDeviceTotalMem, true)
    BIND(cuCtxCreate, true)
    BIND(cuCtxDestroy, true)
    BIND(cuCtxSynchronize, false)
    BIND(cuModuleLoadData, false)
    BIND(cuModuleGetFunction, false)
    BIND(cuMemAlloc, true)
    BIND(cuMemFree, true)
    BIND(cuMemGetInfo, true)
    BIND(cuMemcpyHtoD, true)
    BIND(cuMemcpyDtoH, true)
    BIND(cuMemsetD8, true)
    BIND(cuMemsetD32, true)
    BIND(cuLaunchKernel, false)
    BIND(cuGetErrorString, false)
#undef BIND
    if (cuda->cuInit(0) != 0) {
        *error = "cuInit failed (driver/library mismatch or no usable GPU)";
        return false;
    }
    return true;
}

static bool describe(kh_cuda_t *cuda, int ordinal, const char **error) {
    int major = 0, minor = 0;
    size_t total = 0;
    if (cuda->cuDeviceGet(&cuda->device, ordinal) != 0 ||
        cuda->cuDeviceGetName(cuda->name, sizeof cuda->name, cuda->device) != 0 ||
        cuda->cuDeviceGetAttribute(&major, ATTRIBUTE_MAJOR, cuda->device) != 0 ||
        cuda->cuDeviceGetAttribute(&minor, ATTRIBUTE_MINOR, cuda->device) != 0 ||
        cuda->cuDeviceTotalMem(&total, cuda->device) != 0) {
        *error = "cannot query CUDA device";
        return false;
    }
    cuda->arch = (unsigned)(major * 10 + minor);
    cuda->total_bytes = total;
    return true;
}

bool kh_cuda_open(kh_cuda_t *cuda, int device, const kh_cuda_image_t *images, unsigned count,
                  const char **error) {
    memset(cuda, 0, sizeof *cuda);
    if (!load(cuda, error) || !describe(cuda, device, error)) {
        return false;
    }
    if (cuda->cuCtxCreate(&cuda->context, 0, cuda->device) != 0) {
        *error = "cannot create CUDA context";
        return false;
    }

    // Exact cubin first; otherwise the PTX image is JIT-compiled by the driver.
    for (int pass = 0; pass < 2 && cuda->module == NULL; ++pass) {
        for (unsigned index = 0; index < count; ++index) {
            const kh_cuda_image_t *image = &images[index];
            bool wanted = pass == 0 ? (!image->ptx && image->arch == cuda->arch)
                                    : (image->ptx && image->arch <= cuda->arch);
            if (wanted && cuda->cuModuleLoadData(&cuda->module, image->data) == 0) {
                break;
            }
            if (wanted) {
                cuda->module = NULL;
            }
        }
    }
    if (cuda->module == NULL) {
        *error = "no embedded CUDA image loads on this device";
        return false;
    }
    return true;
}

void kh_cuda_close(kh_cuda_t *cuda) {
    if (cuda->context != NULL && cuda->cuCtxDestroy != NULL) {
        cuda->cuCtxDestroy(cuda->context);
    }
    if (cuda->library != NULL) {
        dlclose(cuda->library);
    }
    memset(cuda, 0, sizeof *cuda);
}

void *kh_cuda_function(kh_cuda_t *cuda, const char *name) {
    void *function = NULL;
    return cuda->cuModuleGetFunction(&function, cuda->module, name) == 0 ? function : NULL;
}

const char *kh_cuda_error(kh_cuda_t *cuda, int status) {
    const char *text = NULL;
    if (cuda->cuGetErrorString == NULL || cuda->cuGetErrorString(status, &text) != 0 || text == NULL) {
        return "unknown CUDA error";
    }
    return text;
}

int kh_cuda_launch(kh_cuda_t *cuda, void *function, unsigned blocks, unsigned threads, void **arguments) {
    if (blocks == 0) {
        return 0;
    }
    return cuda->cuLaunchKernel(function, blocks, 1, 1, threads, 1, 1, 0, NULL, arguments, NULL);
}

int kh_cuda_probe(void) {
    kh_cuda_t cuda = {0};
    const char *error = NULL;
    int count = 0;
    if (!load(&cuda, &error) || cuda.cuDeviceGetCount(&count) != 0) {
        fprintf(stderr, "%s\n", error != NULL ? error : "cannot count CUDA devices");
        kh_cuda_close(&cuda);
        return 1;
    }
    for (int index = 0; index < count; ++index) {
        if (describe(&cuda, index, &error)) {
            printf("{\"index\":%d,\"name\":\"%s\",\"arch\":%u,\"total_bytes\":%llu}\n",
                   index, cuda.name, cuda.arch, (unsigned long long)cuda.total_bytes);
        }
    }
    kh_cuda_close(&cuda);
    return 0;
}
