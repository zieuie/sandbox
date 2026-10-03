# GPU matching solver

Exact maximum bipartite matching for one primitive-X field on one GPU. It is a
drop-in for `matching_solver/kh_match_kernel`: same blocks input, packed payload,
metadata JSON and exit codes. Its certificates are ordinary KHM1 files, checked
by the existing independent verifier. The cluster runs it as the `match_gpu`
program; see [docs/GPU.md](../docs/GPU.md) for scheduling, fleet and operations.

## Results

### RTX 3060 Laptop (Merlin, 6 GiB)

All runs used the same KHD1 inputs as the campaign. The pinned polynomials match
the campaign certificates, except 3^17 and 2^27, which use the first automatic
candidate. "Cluster" is the measured `match_distributed` wall time on 9 nodes
(16 threads each). "`match`, 2 thr." is the single-node CPU run from the
overnight campaign. "GPU solve" is greedy plus augmentation on the device.
"Native wall" is the whole `kh_gpu_match_kernel` process: CPU field
construction, upload, solve and payload write.

| Field | q | F | Cluster (9 nodes) | `match`, 2 thr. | GPU solve | Phases | Native wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2^23 | 8.4 M | 2048 | 906 s | 774 s | **0.19 s** | 2 | — |
| 3^15 | 14.3 M | 2187 | 1,964 s | 2,902 s | **0.34 s** | 2 | — |
| 11^7 | 19.5 M | 1331 | 2,306 s | 2,039 s | **0.48 s** | 2 | — |
| 2^25 | 33.6 M | 4096 | 5,264 s | 10,790 s | **0.81 s** | 2 | — |
| 7^9 | 40.4 M | 2401 | 4,031 s | 4,290 s | **0.88 s** | 2 | — |
| 5^11 | 48.8 M | 3125 | 6,698 s | 12,823 s | **1.07 s** | 1 | — |
| 13^7 | 62.7 M | 2197 | 6,584 s | — | **1.5 s** | 2 | 9.5 s |
| 3^17 | 129 M | 6561 | not admitted | — | **3.2 s** | 2 | 25.6 s |
| 2^27 | 134 M | 8192 | not admitted | — | **3.6 s** | 2 | 11.0 s |

The full independent Python KHM1 verifier accepted the certificates for 13^7
(173 s), 2^25 (130 s), 3^17 (542 s) and 2^27 (753 s). Verification, not
solving, is now the slowest step.

### Quadro P600 (.101–.105, 2 GiB)

5^11 took 34.2 s of wall time: 21.1 s CPU field construction on the i7-7700T,
3.9 s greedy, 4.4 s augmentation and 4.7 s payload write. The 9-node cluster
took 6,698 s. P600 nodes hold fields up to about 55 M labels.

### Where the speedup comes from

The same algorithm ported to one CPU core (`src/cpu_reference.c`) shows most of
the gain is algorithmic. Measurements on 2^23, same polynomial:

| Implementation | Solve | Edge scans |
| --- | ---: | ---: |
| Production `kh_match_kernel`, 1 thread | 1,085 s | 44.3 G |
| `cpu_reference`, 1 core, greedy from k=0 + APFB | 71.3 s | 8.67 G |
| `cpu_reference`, 1 core, offset greedy + APFB | **4.10 s** | 143 M |
| GPU, greedy from k=0 + APFB | 2.02 s | 8.89 G |
| GPU, offset greedy + APFB | **0.19 s** | 421 M* |

\*The GPU counts whole 32-lane warp chunks, so it overcounts compared with the
serial early exit.

Two changes explain the gap:

1. **Greedy start offset.** Each request starts its neighbor scan at a hashed
   offset rather than `k = 0`. Otherwise every request contends for the same
   early neighbors.
2. **APFB phases instead of shortest-layer Hopcroft–Karp.** Production phase 1
   alone scanned 25.8 G edges, because its BFS keeps scanning after a free
   neighbor is found. APFB stops each tree at its first free endpoint, and
   typical fields finish in 1–2 phases.

On top of the algorithm, the GPU adds about 20× over one core, because 32-wide
warps hide the latency of the random `right[]` gathers.

**Recommended CPU follow-up:** port these two changes into `kh_match_kernel`. It
would help fields that exceed every GPU here (17^7, 2^29, 19^7, 7^11, 11^9).
The kernel needs about 32 bytes per label, so 2^29 fits in Merlin's RAM, on CPU,
in minutes. This work did not change the CPU matcher.

## Algorithm and correctness

1. **Greedy:** one warp per request. Lanes read 32 consecutive cell labels
   (coalesced), apply the `X^-coset` shift, and gather `right[v]`. The lowest
   free lane is claimed with `atomicCAS(right[v], FREE, u)`.
2. **APFB phase:** `collect_roots` lists the free requests and gives each one a
   root index. `expand` is called once per BFS level, one warp per frontier
   vertex. A matched left vertex `w` joins exactly one tree, by
   `atomicCAS(root[w], FREE, r)`. When a tree finds a free right vertex, it
   first claims its root (`rootdone[r]`), then reserves the endpoint
   (`right[v]: FREE → RESERVED`). If the reservation fails, it releases the
   root. A tree stops expanding once its root is claimed.
3. **Augment:** trees are vertex-disjoint and reserved endpoints are distinct,
   so one thread per claimed root flips its path without conflicts.
4. **Termination:** a phase that claims nothing has explored the whole
   alternating closure from every free request, with no tree pruned. So no
   augmenting path exists (Berge) and the matching is maximum. The visited set
   is then a Hall witness, `|N(S)| = |S| − deficiency`. It is written as the
   KHM1 obstruction bitmap.
5. **Checks:** an on-device pass re-derives every selected edge from the cell
   table and checks ownership in both directions. The cluster then runs the
   independent Python KHM1 verifier before publication.

`tests/check.py` runs four real fixtures through the verifier. In automatic
mode it checks the polynomial agrees with the CPU kernel. It also runs 60
random synthetic cell tables, injected with the test-only `--test-cells` option,
against a brute-force maximum matching. That includes about 35 certified Hall
obstructions, each checked for deficiency.

## Memory

Device memory is `8q + 24N + 18(N/8 + 1) + 16·blocks + 16 MiB`:

- the cell table and `right[]`;
- `left`, `choice` (u16), `root`, `parent`, via-k (u16) and two frontier
  queues;
- per-root claim state, budgeted at N/8. After greedy it is only a few
  thousand roots.

Every campaign field has N = q, so this is about 32 bytes per label.
`gpu_match_solver/adapter.py:device_bytes` mirrors the formula, and the leader
fences on it. Host memory is about 4q for the field plus downloads. The cluster
also budgets about 24q + 512 MiB for the Python verifier, which needs about
18 bytes per label.

| GPU | Usable | Largest field |
| --- | ---: | --- |
| RTX 3060 Laptop (Merlin) | 5.6 GiB | ~180 M labels: 3^17, 2^27 |
| Quadro P600 (.101–.105) | 1.7 GiB | ~55 M labels: 5^11 |

## Build, run, test

The kernel source is `src/kernels.cu`. The NVRTC-compiled images (cubins for
sm_61/75/86/89 plus compute_61 PTX) are committed in `src/kernels_images.c`. A
normal build needs only `cc`, and the binary needs only the NVIDIA driver
(`libcuda.so.1`, loaded with `dlopen`). See [../cuda/README.md](../cuda/README.md).

```sh
make -C king_hamming/gpu_match_solver               # kernel + investigation helpers
make -C king_hamming/gpu_match_solver check         # fixtures + synthetic oracle
python3 king_hamming/gpu_match_solver/tests/check_cluster.py --run   # leader+agents e2e
make -C king_hamming/cuda toolchain && make -C king_hamming/gpu_match_solver images  # after editing kernels.cu
```

```sh
./kh_gpu_match_kernel P R BLOCKS.txt PAYLOAD.bin [--poly C0,...,Cr] [--threads N]
    [--max-bytes N] [--device N] [--max-device-bytes N] [--salt N]
```

`--threads` only affects CPU field construction. The kernel has no checkpoints;
a run takes seconds, and a lost lease simply reruns. Limits: `F ≤ 65535` (16-bit
choices) and `q ≤ 2^32 − 1`.

To queue a field by hand:

```sh
python3 king_hamming/gpu_match_solver/submit.py FIELD.khdp --poly C0,...,Cr \
    --leader http://192.168.4.151:8061 --enqueue
```

## Files

| Path | Purpose |
| --- | --- |
| `src/kernels.cu`, `src/kernels_images.c` | CUDA kernels and committed NVRTC images |
| `src/main.c` | `kh_gpu_match_kernel` host program |
| `adapter.py`, `cluster_solver.py`, `submit.py` | `match_gpu` cluster adapter, bridge, specification builder |
| `tests/check.py`, `tests/check_cluster.py` | kernel oracle tests and end-to-end cluster test |
| `gpu_match.py`, `src/field_export.c`, `src/cpu_reference.c` | original CuPy prototype, field dump and single-core reference (investigation) |
| `results/` | raw measurement logs |
| `PROGRESS.md` | overnight work log |
