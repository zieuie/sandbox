# GPU block matching solver

Exact perfect matching for one primitive-X field that is **larger than one GPU's
memory**. The field is cut into blocks of cells, each with a window of right
vertices of the same size. Blocks are matched one after another on one GPU, then
requests left over are imported into other blocks that still have free rights.
The design is in [docs/GPU_BLOCK_MATCHING.md](../docs/GPU_BLOCK_MATCHING.md).

This is a **separate solver** from [gpu_match_solver](../gpu_match_solver/README.md).
Fields that fit one GPU should keep using `kh_gpu_match_kernel`: it matches the whole
graph at once, can prove that no perfect matching exists (a Hall certificate, exit 2),
and is the production path today. This one never certifies an obstruction; a field it
cannot finish ends as "incomplete" (exit 4).

Same input and payload contract as `kh_gpu_match_kernel`: blocks file, packed
choices, one JSON metadata line. Output is an ordinary KHM1 certificate after
`matching_solver.artifacts.publish`, checked by the independent verifier.

```sh
make -C king_hamming/gpu_block_match_solver          # kernel binary
make -C king_hamming/gpu_block_match_solver check    # fixtures, window oracle, exchange tests
make -C king_hamming/cuda toolchain && make -C king_hamming/gpu_block_match_solver images  # after editing kernels.cu
```

```sh
./kh_gpu_block_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr] [--threads N]
    [--max-bytes N] [--device N] [--salt N] [--block-device-bytes N]
    [--block-requests N] [--max-rounds N] [--max-residual N]
```

Exit 0: full matching. Exit 4: incomplete (metadata `incomplete: true`, no payload).
Exit 1: error. Limits: `F <= 65534`, `q <= 2^32 - 1`. Host memory is about
`4q + 2n` bytes (full field table) until the prefix-only builder of design stage 2 exists.

## Results (RTX 3060 Laptop, merlin)

`results/blocks.jsonl` has the raw records. `tests/bench_blocks.py FIELD --blocks 2,4,8`
reruns them; `--blocks 0` sizes blocks from free device memory.

| Field | Blocks tried | Round-1 residual | Result |
| --- | --- | ---: | --- |
| 2^23, 5^11, 13^7, 3^17, 2^27 | 2, 4, 8, 16, 32, 64 each | 0 in all 30 runs | full matching in round 1 |

These fields also fit one GPU, so their answers were known. Independent verifier
(Python KHM1) accepted the 2^23 runs at 4, 8, 16 and 32 blocks. Blocks must have nearly
equal request counts: with a small remainder block (13,650 of 8.4 M requests) 2^23 left
about 2,000 requests unmatched and the exchange could not finish them.

## In the campaign

Program `match_gpu_blocks` (`adapter.py`, bridge `cluster_solver.py`, spec builder
`submit.py`). The feeder plans it only when no GPU holds the whole field
([docs/GPU.md](../docs/GPU.md), "Feeder routing"); the lease is fenced to the largest GPU.
Optional spec argument `block_device_bytes` forces smaller blocks (tests).
`tests/check_cluster.py --run` runs a forced 4-block field through a real leader and agent
and verifies it.

## Files

| Path | Purpose |
| --- | --- |
| `src/kernels.cu`, `src/kernels_images.c` | CUDA kernels (window scan, restore, APFB) and committed NVRTC images |
| `src/main.c` | `kh_gpu_block_kernel`: layout, block runs, exchange rounds, payload |
| `tests/check.py` | fixtures through the verifier; per-block exact-matching oracle for the window; exchange cases |
| `adapter.py`, `cluster_solver.py`, `submit.py` | `match_gpu_blocks` cluster adapter, bridge, specification builder |
| `tests/check_cluster.py` | end-to-end run through an isolated leader and GPU agent |
| `tests/bench_blocks.py` | residual/round/time measurements on saved fields |
