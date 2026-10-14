# GPU symmetric template climber. Kernels are compiled with NVRTC (build
# machine only, via ../king_hamming/cuda/.venv) and embedded, so the binary
# needs only libc and the NVIDIA driver on the GPU host.
CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra
CUDA_DIR = ../king_hamming/cuda
EMBED = $(CUDA_DIR)/.venv/bin/python $(CUDA_DIR)/embed.py

all: gpuclimb cpu/symclimb3 cpu/symclimb2 cpu/symclimb

gpuclimb: gpuclimb.c kernels_images.c $(CUDA_DIR)/kh_cuda.c $(CUDA_DIR)/kh_cuda.h
	$(CC) $(CFLAGS) -I$(CUDA_DIR) gpuclimb.c kernels_images.c $(CUDA_DIR)/kh_cuda.c -ldl -o $@

kernels_images.c: kernels.cu
	$(EMBED) kernels.cu chebyshev_images -o $@

cpu/%: cpu/%.c
	$(CC) -O3 -march=native $< -lm -o $@

clean:
	rm -f gpuclimb cpu/symclimb cpu/symclimb2 cpu/symclimb3

.PHONY: all clean
