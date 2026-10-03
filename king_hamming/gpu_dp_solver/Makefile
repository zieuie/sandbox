CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../cuda -I../dp_solver/include

DP = ../dp_solver/src/transitions.c ../dp_solver/src/common.c
EMBED = ../cuda/.venv/bin/python ../cuda/embed.py

.PHONY: all images check bench clean
all: kh_gpu_dp_tile

kh_gpu_dp_tile: src/main.c src/kernels_images.c ../cuda/kh_cuda.c ../cuda/kh_cuda.h $(DP) ../dp_solver/include/kh_solver.h
	$(CC) $(CPPFLAGS) $(CFLAGS) src/main.c src/kernels_images.c ../cuda/kh_cuda.c $(DP) -ldl -lm -o $@

# Regenerate committed kernel images after editing src/kernels.cu (needs make -C ../cuda toolchain).
images:
	$(EMBED) src/kernels.cu kh_dp_images -o src/kernels_images.c

check: all
	./kh_gpu_dp_tile >/dev/null
	python3 tests/check.py --run

bench: all
	python3 tests/bench.py --run

clean:
	rm -f kh_gpu_dp_tile
