# Symmetric template search for Chebyshev permutation arrays

These programs look for permutation arrays that meet the binomial bound
P(n,d) ≤ C(n,d)·P(n−d,d), Theorem 12 of the Bereg et al. Chebyshev paper.
They only search arrays that are invariant under a group of symmetries, which
shrinks the problem a lot.

## The idea

- **Templates.** Split the symbols into blocks of d consecutive values. A row's
  *template* is the block index at each position.
  - Two rows with the same template are never at distance d or more, so an
    array has at most one row per template.
  - When n ≤ 2d there are two blocks, and a template is just the d-subset of
    positions that hold the values 0..d−1. The C(n,d) bound means one row per
    d-subset.
- **Symmetry.** We fix a group G of position permutations, some of which are
  combined with value reversal v ↦ n−1−v, and look only for arrays that G maps
  to themselves.
  - If G acts freely on templates, one representative row per template orbit
    determines the whole array.
  - For n = 2d the group is x ↦ ±x + b mod (2d−1) on positions 0..2d−2, with
    position 2d−1 fixed; the "−" elements also reverse values. It has order
    2(2d−1) and acts freely on d-subsets.
  - For n = 2d−1 the group is the cyclic shifts Z_n.
- **Search.** The search works on the representatives only. A move swaps two
  positions of a representative that lie in the same block, so the template is
  kept. The score counts row pairs at distance < d, using the symmetry to
  compare a representative only against the other rows.

| case | reps | rows | CPU (`cpu/symclimb3`) | GPU (`gpuclimb`, T1000) |
|---|---|---|---|---|
| (12,6) | 42 | 924 | ~1 s | <1 s |
| (14,7) | 132 | 3,432 | 86 s | 18 s |
| (16,8) | 429 | 12,870 | open | open (running) |

The verified P(14,7) = 3,432 array is in `results/pa_14_7_3432.txt`.

## Programs

- **`cpu/symclimb.c`, `cpu/symclimb3.c`.** Simulated annealing at a fixed
  temperature, for n ∈ {2d−1, 2d}. Version 3 keeps a table of separation counts
  so that a swap re-checks only the two positions it changes.
  - Usage: `symclimb3 n d seconds seed temp`
- **`cpu/symclimb2.c`.** The general multi-block version. Its template is the
  full block pattern, with one row per multinomial template, e.g. (9,3) and
  (12,4). Groups are `cyc` or `dih`, with foe pruning.
  - Usage: `symclimb2 n d seconds seed cyc|dih|none temp`
- **`cpu/p74lem.c`.** The exact search that found P(7,4) = 35. It builds in
  the forced rule s(x) = r(x) + 4.
- **`gpuclimb` (`gpuclimb.c` + `kernels.cu`).** Tabu search, one thread block
  per independent chain.
  - Each step takes a conflicted representative and scores **all** of its
    swaps against every row at once. It then applies the best swap that is not
    tabu, with a little random noise and a random kick when it stalls.
  - At the end the host expands the best array and checks every pair before
    writing `pa_n_d_gpu.txt`.
  - Usage: `gpuclimb n d seconds [--chains C] [--steps K] [--noise p] [--tenure lo span] [--stall S] [--seed X]`

## Running on the cluster: live status, stop and resume

- **Status.** gpuclimb writes `status.json` in its working directory every
  30 s. The fields are cell, target rows, best and mean conflicts, hours and
  steps so far, and a state of running, solved, stopped or timeout.
  `./status.sh` (run from uther) prints one line per search on every machine.
- **Stop and resume.** Every chain's best array is saved to
  `state_<n>_<d>_<rows>.bin` every `--save-every` seconds (default 300) and on
  SIGINT/SIGTERM. A later run with the same n, d and target resumes all chains
  from that file, carrying its cumulative time and step counts.
- **Ladders.** `ladder.sh n d m0 step try_secs total_secs [gpuclimb args]`
  keeps the current target in `ladder.m` and moves to m+step whenever a try
  ends with a verified array. Live output of the current try is in `try.log`.
  To stop it, `kill -TERM <ladder pid>`; the state is saved. To continue,
  start the same command again in the same directory.
  - Example: `ladder.sh 15 8 229 8 1800 0 --chains 24` (total 0 means run
    until stopped).

## Building

`make` builds everything. The GPU kernel is compiled by NVRTC from
`../king_hamming/cuda/.venv` and embedded in `kernels_images.c`, which is
committed. The binary therefore needs only libc and the NVIDIA driver, so it
can be copied to any GPU host, for example `scp gpuclimb gawain:chebyshev_gpu/`.
