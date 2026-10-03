# GPU DP tile solver

`kh_gpu_dp_tile` computes one exact DP tile on a GPU. It is a drop-in for
`dp_solver/kh_dp_tile`: same arguments, same memory admission, and
**byte-identical** `values.bin`, `choices.bin` and `tile.json`. Because the
output is identical, the cluster uses it opportunistically. DP specifications,
tile layouts, checkpoints and artifacts are unchanged. A tile lease runs this
binary when the host GPU is free and falls back to `kh_dp_tile` otherwise (see
[docs/GPU.md](../docs/GPU.md)).

## Results

These are interior production tiles at side 4096, the tile size the live 13^9
and 23^7 roots use, with a full `p²` halo of random predecessor values. "CPU" is
`kh_dp_tile` with 2 threads, the default tile team, pinned to two free Merlin
CPUs. Each GPU result was compared byte for byte with the CPU output.

| Tile (4096²) | Transitions | CPU, 2 threads | RTX 3060 Laptop | Speedup | Quadro P600 | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 13^9 | 1,838 | 59.9 s | **0.92 s** | 65× | 3.7 s | 16× |
| 23^7 | 9,969 | 338.2 s | **2.36 s** | 143× | 20.0 s | 17× |

GPU times are whole-process wall times, including CUDA context creation (about
0.15 s), upload and download. Small tiles are dominated by that setup: a
512-side tile is only 1.3–2.6× faster for 13^9, but 12–14× for 23^7.

For context, live campaign tile leases currently take a median of 73 s for
13^9 and 567 s for 23^7 end to end, including halo transfer. Compute was the
dominant cost for 23^7. After this change, tile throughput on GPU hosts is
bounded by halo assembly and transfer
([DP_NETWORK_LOCALITY.md](../docs/DP_NETWORK_LOCALITY.md)).

Raw numbers are in `results/bench.jsonl`, `results/bench_4096.log` and
`../gpu_match_solver/results/p600_bench.jsonl`.

## Design

Every transition `(a, b, t)` has `a, b, t ≥ 1`, so cell `(u, v)` depends only on
cells in strictly earlier rows. One kernel launch computes a whole tile row:

- Each block owns 32 consecutive cells, one per lane. For a fixed transition,
  the lanes read 32 consecutive predecessor values, which coalesces.
- The block's warps (8–32 of them, by transition count) split the ordered
  transition table into contiguous slices. Each slice keeps its best candidate
  and the first index that reaches it, using strict `>`.
- Slices merge in order, again with strict `>`. The result is exactly the CPU
  kernel's sequential rule: the earliest transition in scan order that attains
  the maximum, choice 0 when no candidate beats the initial 0, and uint64
  wraparound.
- The whole halo-plus-tile rectangle stays on the device, and rows run in order
  on one stream. A 4096 tile with a 23² halo needs about 250 MB, which fits a
  P600.

The host program mirrors `kh_dp_tile`. It uses the same coordinate checks and
the same admission arithmetic, so `memory_payload_bytes` in `tile.json` matches.
It also has the same staging directory, `renameat2(RENAME_NOREPLACE)`
publication and fsyncs. It does not set `RLIMIT_AS`, because a CUDA context
reserves far more virtual address space than it uses; host memory is still
bounded by the checked payload. It prints the same progress JSON shape, with
`"engine":"gpu"`.

Exit status 3 means no usable GPU, or that a CUDA call failed. Callers fall
back to the CPU kernel on any nonzero status.

## Build and test

```sh
make -C king_hamming/gpu_dp_solver            # needs only cc; kernel images are committed
make -C king_hamming/gpu_dp_solver check      # 48 random tiles vs kh_dp_tile, byte for byte
python3 king_hamming/gpu_dp_solver/tests/bench.py --run --side 4096 --cpus 14,15
make -C king_hamming/cuda toolchain && make -C king_hamming/gpu_dp_solver images   # after editing kernels.cu
```

The check covers nine `(p, r)` shapes, boundary tiles (`first_u = 1`), random
values, tie-heavy values (0–3), all-zero halos and near-`2^64` values that
wrap. The cluster end-to-end test
(`gpu_match_solver/tests/check_cluster.py`) runs a distributed 5^3 root that
mixes GPU and CPU tiles. It checks the result against `kh_dp_local`.
