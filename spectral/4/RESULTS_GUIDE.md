# How to read the unrestricted-weight and theta results

Read this before analyzing `results/`. This phase contains two different
spectral bounds for the forbidden-distance graph (H_{n,d}), whose independent
sets are Chebyshev permutation codes.

## Files

- `p53_unrestricted_weighted.json`: one variable for every inverse pair of
  forbidden permutations for (P(5,3)).
- `theta_bounds.json`: Fourier-domain Lovász theta results.
- `theta_report.md`: short human-readable summary.

## Unrestricted weighted Hoffman

The weights are normalized so every nontrivial Fourier block has minimum
eigenvalue at least \(-1\). If their row sum is (R), the bound is

\[
P(n,d)\le \frac{n!}{1+R}.
\]

Important fields:

- `number_of_variables`: number of inverse-pair weight variables;
- `weight_classes`, `class_sizes`, `weights`: parallel arrays describing the
  solution;
- `row_sum`: independently equals the class-size-weight dot product;
- `spectral_upper_bound_real` and `spectral_upper_bound_floor`: real and integer
  forms of the ratio bound;
- `active_partitions`: irreducible blocks numerically tight at \(-1\);
- `minimum_block_eigenvalue`: final feasibility check;
- `feasibility_rescale`: uniform correction for solver-scale PSD error.

For (P(5,3)), the unrestricted result is approximately `10.0000007`, which
numerically recovers the known exact integer bound (10). It is not by itself
an exact symbolic certificate.

## Fourier-domain Lovász theta

Each theta record contains one PSD matrix variable per irreducible partition.
The reported objective is an upper bound on (P(n,d)).

- `theta_value`: numerical SDP objective;
- `integer_upper_bound`: near-integers within `integer_snap_tolerance` are
  snapped before reporting, avoiding false claims caused by solver error;
- `number_of_forbidden_permutations`: original edge-difference count;
- `number_of_edge_constraints_after_inversion`: equality count after merging
  (x\) with (x^{-1});
- `normalization_residual` and `max_edge_constraint_residual`: equality checks;
- `minimum_psd_eigenvalue`: worst independently recomputed PSD eigenvalue;
- `max_constraint_residual`: maximum of all reported feasibility violations;
- `nonzero_blocks`: partitions whose solution matrices have visible numerical
  norm, with trace and Frobenius norm.

The model was separately tested on an edgeless forbidden graph, where theta is
\(|S_n|\), and a complete forbidden graph, where theta is (1).

## Current conclusions

- (P(5,3)): theta is approximately `10`, matching the known exact value.
- (P(7,4)): theta is approximately `35`, reproducing but not improving the
  known upper bound (35).

The (P(7,4)) SCS solution has a small negative PSD eigenvalue on the order of
`1e-7`. Thus it is strong numerical evidence that the optimum is (35), not an
exact certificate that theta equals (35). In particular, do not interpret the
printed value slightly below (35) as proving (P(7,4)\le34).

Clarabel solved the (P(5,3)) problem but failed on the larger (P(7,4)) cone
model in this environment. The saved full run uses SCS.

## Suggested analysis questions

1. Which irreducible blocks carry the theta solution for each case?
2. Does the unrestricted weighted solution suggest simple exact weights?
3. Can the (P(5,3)) value (10) be converted into an exact weighted
   certificate?
4. Can a primal or dual exact construction explain the apparent theta value
   (35) for (P(7,4))?
5. Which method—structured weighting, unrestricted weighting, or theta—accounts
   for each improvement?

## Suggested prompt

> Read `RESULTS_GUIDE.md` and `LOVASZ_THETA_NEXT_PHASE.md`, then analyze
> `p53_unrestricted_weighted.json`, `theta_bounds.json`, and `theta_report.md`.
> Treat the (P(7,4)) value near 35 as 35 at solver precision, not as an integer
> improvement. Examine active/nonzero irreducible blocks and look for exact
> certificates or structural explanations. Clearly distinguish numerical
> evidence, independently checked constraints, exact facts, and conjectures.
