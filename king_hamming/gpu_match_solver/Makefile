CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -Iinclude -I../cuda -I../dp_solver/include -I../matching_solver/include

SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c
CUDA = ../cuda/kh_cuda.c ../cuda/kh_cuda.h
EMBED = ../cuda/.venv/bin/python ../cuda/embed.py

.PHONY: all images venv check bench clean
all: kh_gpu_match_kernel build/kh_field_export build/kh_cpu_reference

kh_gpu_match_kernel: src/main.c src/kernels_images.c $(CUDA) $(SHARED) ../matching_solver/src/resource.c ../dp_solver/include/kh_field.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/main.c src/kernels_images.c ../cuda/kh_cuda.c ../matching_solver/src/resource.c $(SHARED) -ldl -o $@

# Regenerate committed kernel images after editing src/kernels.cu (needs make -C ../cuda toolchain).
images:
	$(EMBED) src/kernels.cu kh_match_images -o src/kernels_images.c

build/kh_field_export: src/field_export.c $(SHARED) ../dp_solver/include/kh_field.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/field_export.c $(SHARED) -o $@

build/kh_cpu_reference: src/cpu_reference.c
	mkdir -p build
	$(CC) $(CFLAGS) src/cpu_reference.c -o $@

# Optional CuPy prototype environment (gpu_match.py).
venv:
	python3 -m venv .venv
	.venv/bin/pip install "cupy-cuda12x[ctk]" numpy

check: all
	./kh_gpu_match_kernel >/dev/null
	python3 tests/check.py --run

clean:
	rm -rf build kh_gpu_match_kernel
