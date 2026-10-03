# CUDA support without a CUDA toolkit

The GPU solvers use the CUDA **driver API**, loaded with
`dlopen("libcuda.so.1")`. Their kernels are compiled at build time by NVRTC
into images that are embedded in the binaries. Each deployed binary depends only
on libc and the NVIDIA driver that GPU hosts already have. No CUDA toolkit,
CuPy, `nvcc` or `libcudart` is needed on the fleet. The pip wheels for CUDA 12
do not include a usable `nvcc` anyway.

| File | Purpose |
| --- | --- |
| `kh_cuda.h`, `kh_cuda.c` | Driver entry points, context and module setup, image selection, launch helper |
| `embed.py` | Compiles a `.cu` file with NVRTC into cubins (sm_61, 75, 86, 89) and compute_61 PTX, as a C array file |
| `probe.c` → `kh_cuda_probe` | Lists usable devices as JSON lines; agents use it to advertise GPUs |

`kh_cuda_open` loads the exact-architecture cubin first. Otherwise the driver
JIT-compiles the PTX, which covers newer GPUs. Pascal (the P600 and
1050 Ti) needs CUDA 12-era images and the 580 driver branch. CUDA 13 dropped
Pascal.

```sh
make -C king_hamming/cuda                 # kh_cuda_probe
./king_hamming/cuda/kh_cuda_probe         # {"index":0,"name":"...","arch":86,"total_bytes":...}
make -C king_hamming/cuda toolchain       # .venv with nvidia-cuda-nvrtc-cu12 (build machine only)
```

Regenerate the committed `*_images.c` with `make images` in
`gpu_match_solver/` or `gpu_dp_solver/` after editing their `src/kernels.cu`.
