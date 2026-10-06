CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../cuda -I../dp_solver/include -I../matching_solver/include

SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c
CUDA = ../cuda/kh_cuda.c ../cuda/kh_cuda.h
EMBED = ../cuda/.venv/bin/python ../cuda/embed.py

.PHONY: all images check clean
all: kh_gpu_wide_kernel kh_verify_wide

# The verifier shares no code with the solver (or with dp_solver), by design.
kh_verify_wide: src/verify_wide.c
	$(CC) $(CFLAGS) -pthread src/verify_wide.c -o $@

kh_gpu_wide_kernel: src/main.c src/field_walk.c src/field_walk.h src/kernels_images.c $(CUDA) $(SHARED) ../matching_solver/src/resource.c ../dp_solver/include/kh_field.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/main.c src/field_walk.c src/kernels_images.c ../cuda/kh_cuda.c ../matching_solver/src/resource.c $(SHARED) -ldl -o $@

tests/field_walk_unit: tests/field_walk_unit.c src/field_walk.c src/field_walk.h $(SHARED)
	$(CC) $(CPPFLAGS) -Isrc $(CFLAGS) -pthread tests/field_walk_unit.c src/field_walk.c $(SHARED) -o $@

tests/bench_walk: tests/bench_walk.c src/field_walk.c src/field_walk.h $(SHARED)
	$(CC) $(CPPFLAGS) -Isrc $(CFLAGS) -pthread tests/bench_walk.c src/field_walk.c $(SHARED) -o $@

# Regenerate committed kernel images after editing src/kernels.cu (needs make -C ../cuda toolchain).
images:
	$(EMBED) src/kernels.cu kh_wide_images -o src/kernels_images.c

check: all tests/field_walk_unit
	./tests/field_walk_unit
	./kh_gpu_wide_kernel >/dev/null
	./kh_verify_wide >/dev/null
	python3 tests/check_verify.py
	python3 tests/check.py --run

clean:
	rm -f kh_gpu_wide_kernel kh_verify_wide tests/field_walk_unit tests/bench_walk
