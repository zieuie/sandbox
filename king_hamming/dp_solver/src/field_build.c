#define _GNU_SOURCE

#include "kh_field.h"

#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

/*
 * Explain the shared field builder and fixed generator convention.
 * Parameters: none.
 * Returns: No value; prints command help and a runnable example.
 */
static void help(void) {
    puts("Build the paper's SUD partition with generator X and stable exponent labels.\n"
         "Usage: ./kh_field P ODD_DEGREE [--threads N] [--start CANDIDATE] [--max-bytes N] [-o FIELD.bin]\n"
         "Example: ./kh_field 13 5 --threads 4 --max-bytes 2147483648 -o /tmp/field.bin\n"
         "Generates the first primitive polynomial in packed lower-coefficient order.\n"
         "Prints its coefficients, bucket sizes and a small sample.\n"
         "Optional output is a native uint32 SUD table in prefix/suffix/label order.\n"
         "Defaults: one pinned thread, start=1, memory limit=2 GiB. No args prints help.");
}

/*
 * Parse dimensions, generate a primitive-X polynomial, and build one shared SUD table.
 * Parameters: argc: Argument count; argv: Input strings described by help.
 * Returns: Zero for help/success; one for invalid input, memory admission or output failure.
 */
int main(int argc, char **argv) {

    // Empty invocation must not allocate a field or contact other machines.
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--help"))) {
        help();
        return 0;
    }

    if (argc < 3) {
        help();
        return 1;
    }
    uint64_t p;
    uint64_t r;

    if (!kh_parse_u64(argv[1], &p) || !kh_parse_u64(argv[2], &r) || p > UINT32_MAX || r > UINT32_MAX) {
        return 1;
    }
    uint64_t threads = 1;
    uint64_t start = 1;
    uint64_t maximum = UINT64_C(2147483648);
    const char *output_path = NULL;

    // Operational controls never change the generator or the mathematical partition.
    for (int index = 3; index < argc; ++index) {
        if (!strcmp(argv[index], "-o") && index + 1 < argc) {
            output_path = argv[++index];
        } else if (index + 1 < argc) {
            const char *option = argv[index];
            uint64_t value;

            if (!kh_parse_u64(argv[++index], &value)) {
                return 1;
            }

            if (!strcmp(option, "--threads")) {
                threads = value;
            } else if (!strcmp(option, "--start")) {
                start = value;
            } else if (!strcmp(option, "--max-bytes")) {
                maximum = value;
            } else {
                help();
                return 1;
            }
        } else {
            help();
            return 1;
        }
    }
    const char *error = NULL;
    kh_parameters_t parameters;

    // Derive q, F and B using the same checked public dimensions as DP.
    if (threads == 0 || threads > UINT32_MAX || start > UINT32_MAX ||
        !kh_parameters((uint32_t)p, (uint32_t)r, &parameters, &error)) {
        fprintf(stderr, "kh_field: invalid dimensions or operational arguments\n");
        return 1;
    }
    uint16_t polynomial[32] = {0};
    uint32_t candidate;

    if (!kh_generate_polynomial(&parameters, (uint32_t)start, polynomial, &candidate)) {
        fprintf(stderr, "kh_field: no primitive polynomial remains in candidate range\n");
        return 1;
    }
    struct rlimit limit;

    // A process address-space ceiling complements the checked construction payload.
    if (getrlimit(RLIMIT_AS, &limit) != 0) {
        return 1;
    }

    if (limit.rlim_cur == RLIM_INFINITY || limit.rlim_cur > maximum) {
        limit.rlim_cur = (rlim_t)maximum;
    }

    if (setrlimit(RLIMIT_AS, &limit) != 0) {
        return 1;
    }
    kh_field_t field;

    if (!kh_build_field(&parameters, polynomial, (uint32_t)threads, maximum, &field, &error)) {
        fprintf(stderr, "kh_field: %s\n", error);
        return 1;
    }
    printf("{\"format\":\"KH-FIELD-1\",\"p\":%u,\"r\":%u,\"q\":%" PRIu64 ",\"suffixes\":%u,\"buckets\":%u,\"generator\":\"X\",\"candidate\":%u,\"polynomial\":[",
           parameters.p, parameters.r, parameters.q, parameters.f, parameters.budget, candidate);

    // Report every polynomial coefficient, low degree first including the monic leading term.
    for (uint32_t index = 0; index <= parameters.r; ++index) {
        printf("%s%u", index ? "," : "", polynomial[index]);
    }
    uint16_t endian = 1;
    printf("],\"byteorder\":\"%s\",\"field_bytes\":%" PRIu64 ",\"threads\":%" PRIu64 "}\n",
           *(unsigned char *)&endian ? "little" : "big", field.allocated_bytes, threads);

    // Print bounded samples rather than a giant grid unless output was explicitly requested.
    for (uint32_t cell = 0; cell < parameters.budget && cell < 3; ++cell) {
        printf("prefix=%u suffix=%u:", cell / parameters.f, cell % parameters.f);

        for (uint32_t index = 0; index < parameters.f && index < 8; ++index) {
            printf(" %u", field.cells[(size_t)cell * parameters.f + index]);
        }
        puts("");
    }
    bool success = true;

    // Retain a table only under an explicitly supplied fresh filename.
    if (output_path != NULL) {
        int descriptor = open(output_path, O_WRONLY | O_CREAT | O_EXCL, 0644);

        if (descriptor < 0) {
            success = false;
        } else {
            FILE *file = fdopen(descriptor, "wb");

            if (file == NULL) {
                close(descriptor);
                success = false;
            } else {
                success = fwrite(field.cells, sizeof *field.cells, parameters.q, file) == parameters.q &&
                          fflush(file) == 0 && fsync(fileno(file)) == 0;

                if (fclose(file) != 0) {
                    success = false;
                }
            }

            if (!success) {
                unlink(output_path);
            }
        }
    }
    kh_free_field(&field);
    return success ? 0 : 1;
}
