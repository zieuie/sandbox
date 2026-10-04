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
    [--block-requests N] [--max-rounds N] [--max-residual N] [--choice-file PATH]
```

Exit 0: full matching. Exit 4: incomplete (metadata `incomplete: true`, no payload).
Exit 1: error. Limits: `F <= 65534`, `q < 2^36`. Labels, rights and request counts are 64-bit;
indices inside one block stay 32-bit.

**Host memory** (`host_bytes()` in `adapter.py` mirrors `main()`):

- Rows of the used cells only, built by `src/field_prefix.c`: `4·F·(a_max·F)` bytes. Every
  label is still enumerated, but only rows below `a_max·F` are stored. A label is kept as its
  low 32 bits; above `q = 2^32` each row also has `(q−1) >> 32` breakpoints (the first index
  whose label reaches `h·2^32`), so a row still costs 4 bytes per label.
- Choices, 2 bytes per request. `--choice-file` puts them in a scratch file mapped into
  memory (page cache, written back under pressure); the bridge always passes it.
- The final right-endpoint bitmap (`q/8`), per-block staging (at most a block budget / 8),
  and fixed overhead.

For 13^9 (q = 10.6e9, a_max = 5) that is about 17.7 GiB with the choice file (37.5 GiB
without), against 59 GiB for the old full table. Building those rows took 6.5 min with
8 threads on merlin (`tests/bench_field 13 9 5 8`).

`--label-split-bits B` (tests only) stores B-bit label words, so small fields exercise the
breakpoints that only real fields above 2^32 need.

## Results (RTX 3060 Laptop, merlin)

`results/blocks.jsonl` has the raw records. `tests/bench_blocks.py FIELD --blocks 2,4,8`
reruns them; `--blocks 0` sizes blocks from free device memory.

| Field | Blocks tried | Round-1 residual | Result |
| --- | --- | ---: | --- |
| 2^23, 5^11, 13^7, 3^17, 2^27 | 2, 4, 8, 16, 32, 64 each | 0 in all 30 runs | full matching in round 1 |
| 11^9 (q = 2.36e9), 2026-10-04 | 16 (sized from the 3060) | 0 | full matching in round 1; field 88 s, blocks 147 s, write 180 s, verified (1,340 s) |

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
| `src/field_prefix.c`, `src/field_prefix.h` | 64-bit field arithmetic and the used-rows builder (32-bit label words plus breakpoints) |
| `tests/field_prefix_unit.c` | the builder against `dp_solver/src/field.c` on small fields, including narrow label words |
| `tests/bench_field.c` | times the used-rows build on a real field (CPU and RAM only) |
| `tests/check.py` | fixtures through the verifier; per-block exact-matching oracle for the window; exchange cases |
| `adapter.py`, `cluster_solver.py`, `submit.py` | `match_gpu_blocks` cluster adapter, bridge, specification builder |
| `tests/check_cluster.py` | end-to-end run through an isolated leader and GPU agent |
| `tests/bench_blocks.py` | residual/round/time measurements on saved fields |
