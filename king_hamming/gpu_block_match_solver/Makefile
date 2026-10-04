CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../cuda -I../dp_solver/include -I../matching_solver/include

SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c
CUDA = ../cuda/kh_cuda.c ../cuda/kh_cuda.h
EMBED = ../cuda/.venv/bin/python ../cuda/embed.py

.PHONY: all images check clean
all: kh_gpu_block_kernel

kh_gpu_block_kernel: src/main.c src/field_prefix.c src/field_prefix.h src/kernels_images.c $(CUDA) $(SHARED) ../matching_solver/src/resource.c ../dp_solver/include/kh_field.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/main.c src/field_prefix.c src/kernels_images.c ../cuda/kh_cuda.c ../matching_solver/src/resource.c $(SHARED) -ldl -o $@

tests/bench_field: tests/bench_field.c src/field_prefix.c src/field_prefix.h $(SHARED)
	$(CC) $(CPPFLAGS) -Isrc $(CFLAGS) -pthread tests/bench_field.c src/field_prefix.c $(SHARED) -o $@

tests/field_prefix_unit: tests/field_prefix_unit.c src/field_prefix.c src/field_prefix.h $(SHARED)
	$(CC) $(CPPFLAGS) -Isrc $(CFLAGS) -pthread tests/field_prefix_unit.c src/field_prefix.c $(SHARED) -o $@

# Regenerate committed kernel images after editing src/kernels.cu (needs make -C ../cuda toolchain).
images:
	$(EMBED) src/kernels.cu kh_block_images -o src/kernels_images.c

check: all tests/field_prefix_unit
	./tests/field_prefix_unit
	./kh_gpu_block_kernel >/dev/null
	python3 tests/check.py --run

clean:
	rm -f kh_gpu_block_kernel tests/field_prefix_unit tests/bench_field
