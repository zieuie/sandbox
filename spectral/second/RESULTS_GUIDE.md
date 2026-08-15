# How to read the Phase D results

Read this file before analyzing `results/phase_d_blocks.json` or
`results/phase_d_summary.csv`.

For each `(n,d)`, let (B_{d-1}) be the Chebyshev ball around the identity. For
every partition \(\lambda\vdash n\), the experiment computes the irreducible
ball-operator block

\[
T_\lambda=\sum_{\pi\in B_{d-1}}\rho^\lambda(\pi).
\]

The blocks use Young's real orthogonal representations and are diagonalized
numerically.

## The two extrema are different

Do not combine these questions:

1. `max_nontrivial_block_max` is
   \(\max_{\lambda\ne(n)}\lambda_{\max}(T_\lambda)\). Its negative is
   `least_adjacency_eigenvalue`. `maximizing_partitions` identifies every tied
   partition. The main conjecture is that \((n-1,1)\) always appears here.
2. `min_nontrivial_block_min` is
   \(\min_{\lambda\ne(n)}\lambda_{\min}(T_\lambda)\).
   `minimizing_partitions` identifies every tied partition. Subtracting one
   gives `least_complement_eigenvalue`, which controls the Hoffman clique bound.

The two extrema can—and often do—come from different partitions.

## JSON block fields

Each entry in `partitions` contains:

- `partition`: the partition \(\lambda\), such as `[4,1]`;
- `dimension`: \(f^\lambda=\dim V_\lambda\);
- `block_min` and `block_max`: the extreme eigenvalues of \(T_\lambda\);
- `block_trace`: the sum of the block eigenvalues;
- `eigenvalues`: all \(f^\lambda\) block eigenvalues, including repetitions.

For a nontrivial partition, a block eigenvalue \(\mu\) produces adjacency
eigenvalue \(-\mu\). If \(\mu\) occurs `m` times inside the block, its
contribution to the full adjacency spectrum has multiplicity

\[
m f^\lambda.
\]

Different blocks may contribute the same numerical eigenvalue, so total full
multiplicities must be combined across partitions.

The trivial partition `[n]` is excluded from both global extrema. Its graph
eigenvalue is the degree (n!-|B_{d-1}|), not the negative of its ball
eigenvalue.

## Summary fields

- `ball_size` is \(|B_{d-1}|\).
- `standard_block_min` and `standard_block_max` refer specifically to
  \((n-1,1)\).
- `hoffman_clique_bound` is a real-valued upper bound on the permutation-code
  size. After allowing for numerical error, floor it to obtain an integer
  bound. `null` means this form of Hoffman does not apply.
- `phase_abc_spectrum_verified` means the reconstructed full spectrum was
  compared with the independent dense Phase A--C spectrum and matched to
  tolerance `1e-8`. `false` can simply mean no Phase A--C comparison record was
  available; it does not by itself mean verification failed.

Floating-point values within about `1e-8` should be treated as equal. Tiny
values such as `1e-15` should normally be read as zero. Partition tie lists were
formed using this tolerance.

## Productive questions

1. Does `[n-1,1]` always occur in `maximizing_partitions` outside the complete
   and edgeless extreme cases? List every exception.
2. Which partition families occur in `minimizing_partitions`, and how do they
   vary with `n` and `d`?
3. When are the two extremal partitions the same?
4. How do the Hoffman bounds scale relative to `n!` and `ball_size`?
5. Which block spectra appear integral, and which show recognizable algebraic
   patterns?
6. For `d=2` and `d=n-1`, do the data support the special formulas described in
   `PHASE_D_GUIDE.md`?

## Limits of the data

The files rigorously verify the implemented finite cases up to numerical
tolerance, but finite computations do not prove a general conjecture. They also
do not contain exact clique numbers or code constructions, so a Hoffman bound
cannot be declared sharp without separate matching evidence.

When reporting findings, separate:

- identities checked by the program;
- finite-data observations;
- conjectures suggested by those observations;
- exceptions and degenerate cases.

## Suggested prompt

> Read `RESULTS_GUIDE.md` and `PHASE_D_GUIDE.md`, then analyze
> `phase_d_blocks.json`, using `phase_d_summary.csv` for quick tabulation. Treat
> numerical values within `1e-8` as equal. Analyze the block maximum and block
> minimum as separate extremal questions. List supporting `(n,d)` cases and all
> exceptions. Clearly distinguish verified finite computations from conjectures,
> and do not claim exact clique numbers or general proofs from these files.
