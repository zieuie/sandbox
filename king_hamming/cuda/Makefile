CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror

.PHONY: all toolchain check clean
all: kh_cuda_probe

kh_cuda_probe: probe.c kh_cuda.c kh_cuda.h
	$(CC) $(CFLAGS) probe.c kh_cuda.c -ldl -o $@

# Build-time only: NVRTC for regenerating embedded kernel images (*_images.c).
toolchain:
	python3 -m venv .venv
	.venv/bin/pip install "nvidia-cuda-nvrtc-cu12==12.9.*"

check: kh_cuda_probe
	./kh_cuda_probe --help >/dev/null

clean:
	rm -f kh_cuda_probe
