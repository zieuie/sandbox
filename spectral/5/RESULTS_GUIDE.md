# How to read the theta-prime results

Read this before analyzing the files under `results/`. This phase computes
Schrijver's strengthened Lovász number \(\vartheta'\) for the forbidden-distance
graph. It starts with the Fourier-domain theta SDP from Phase 4 and additionally
requires the reconstructed invariant function

\[
f(g)=\frac1{n!}\sum_{\lambda\vdash n}f^\lambda
\langle A_\lambda,\rho^\lambda(g)\rangle
\]

to be nonnegative for every group element. This is entrywise nonnegativity of
the invariant primal theta matrix.

## Files

- `theta_prime_bounds.json`: complete records for (P(5,3)) and (P(7,4)).
- `theta_prime_report.md`: comparison with ordinary theta and key diagnostics.
- `p74_theta_prime_solution.json`: standalone copy of the target result,
  including the elements attaining the smallest reconstructed (f(g)).

## Important fields

- `theta_prime_value`: numerical SDP objective and upper bound on (P(n,d)).
- `integer_upper_bound`: integer interpretation after snapping values within
  `integer_snap_tolerance` of an integer.
- `normalization_residual`: error in the Fourier trace normalization.
- `max_forbidden_edge_residual`: largest absolute reconstructed numerator on a
  forbidden relative permutation, where it should be zero.
- `minimum_psd_eigenvalue`: smallest independently recomputed eigenvalue among
  all Fourier PSD blocks. A small negative value is solver error.
- `minimum_reconstructed_f_value`: minimum (f(g)) over every element of
  (S_n), recomputed after solving.
- `max_nonnegativity_violation`: negative part of that minimum.
- `number_of_forbidden_constraints`: forbidden equalities after inversion
  reduction.
- `number_of_nonnegativity_constraints`: nonedge inequalities after inversion
  reduction. Forbidden elements already have equality constraints, and the
  identity is fixed by normalization, so neither is duplicated here.
- `number_of_group_elements_checked`: size of the full group used for the final
  diagnostic scan.
- `nonzero_irrep_blocks`: partitions with numerically visible solution blocks.
- `fourier_block_matrices`: the complete numerical \(A_\lambda\) matrices,
  allowing the saved solution to be reconstructed and checked without resolving
  the SDP.
- `elements_attaining_minimum_f`: zero-based tuple permutations at the smallest
  reconstructed value, within the reporting tolerance.

## Normalization and convention validation

Before the research cases were run, identity Fourier blocks on (S_3) were
reconstructed. They give (f(e)=1), (f(g)=0) for (g\ne e), and the primal
matrix (I/6) with trace one. This checks the relative-element convention and
the factor (1/n!\).

The (P(5,3)) strict validation also returns approximately (10), as required
by the known code of size (10) and the ordinary-theta result.

## Current conclusion

The saved SCS results are approximately:

| Case | Ordinary theta | Theta-prime | Integer interpretation |
|---|---:|---:|---:|
| (P(5,3)) | 10 | 10 | 10 |
| (P(7,4)) | 35 | 34.99998 | 35 |

For (P(7,4)), the value below (35) is much closer to (35) than to (34)
and accompanies solver-scale PSD and nonnegativity violations. It must be read
as numerical evidence for theta-prime (=35), not as a proof of the improved
bound (P(7,4)\le34).

Thus entrywise nonnegativity did not improve the known upper bound in this run.
This does not prove symbolically that \(\vartheta'=35\); an exact primal/dual
certificate would be required for that statement.

## Suggested questions

1. Which Fourier blocks differ materially between ordinary theta and
   theta-prime?
2. Which permutations attain the minimum reconstructed (f(g)), and do they
   share a geometric pattern?
3. Can the apparent value (35) be explained by an exact primal or dual
   certificate?
4. Does the failure of theta-prime suggest moving to a multi-point or
   Terwilliger-type SDP rather than further one-matrix refinements?
5. Is the optional (P(5,3)) weighted-Hoffman solution likely to admit the
   proposed exact weights in \(\{0,1/3,2/3\}\)? That side task was not resolved
   by this theta-prime run.

## Suggested prompt

> Read `RESULTS_GUIDE.md` and `THETA_PRIME_NEXT_PHASE.md`, then analyze
> `theta_prime_bounds.json`, `theta_prime_report.md`, and
> `p74_theta_prime_solution.json`. Treat the (P(7,4)) value `34.99998` as 35
> at the recorded solver precision; do not claim an upper bound of 34. Compare
> ordinary theta and theta-prime, examine the nonzero blocks and minimizing
> permutations, and look for exact structural explanations. Clearly separate
> numerical evidence, checked feasibility diagnostics, and exact theorems.
