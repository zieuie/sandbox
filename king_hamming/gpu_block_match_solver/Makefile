CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../cuda -I../dp_solver/include -I../matching_solver/include

SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c
CUDA = ../cuda/kh_cuda.c ../cuda/kh_cuda.h
EMBED = ../cuda/.venv/bin/python ../cuda/embed.py

.PHONY: all images check clean
all: kh_gpu_block_kernel

kh_gpu_block_kernel: src/main.c src/kernels_images.c $(CUDA) $(SHARED) ../matching_solver/src/resource.c ../dp_solver/include/kh_field.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/main.c src/kernels_images.c ../cuda/kh_cuda.c ../matching_solver/src/resource.c $(SHARED) -ldl -o $@

# Regenerate committed kernel images after editing src/kernels.cu (needs make -C ../cuda toolchain).
images:
	$(EMBED) src/kernels.cu kh_block_images -o src/kernels_images.c

check: all
	./kh_gpu_block_kernel >/dev/null
	python3 tests/check.py --run

clean:
	rm -f kh_gpu_block_kernel
