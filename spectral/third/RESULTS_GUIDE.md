# How to read the weighted spectral results

Read this before analyzing the files in `results/`. The experiment assigns
weights to forbidden relative permutations and maximizes their row sum (R)
subject to every nontrivial irreducible block having least eigenvalue at least
\(-1\). A feasible solution gives

\[
P(n,d)\le \frac{n!}{1+R}.
\]

## Weight families

- `shell`: one weight for each nonzero Chebyshev displacement below `d`.
- `shell_cycle_type`: one weight for each pair `(displacement, cycle type)`.

`weight_classes`, `class_sizes`, and `weights` are parallel arrays. Multiplying
each class size by its weight and summing gives `row_sum`.

## Important fields

- `spectral_upper_bound_real` is (n!/(1+R)).
- `spectral_upper_bound_floor` is the resulting integer upper bound, subject to
  the numerical warning below.
- `minimum_block_eigenvalue` should be at least `-1` within tolerance.
- `block_minima` gives the independently recomputed minimum for every nontrivial
  partition.
- `active_partitions` lists blocks whose minimum is approximately `-1`.
- `ordinary_hoffman` gives the unweighted comparison using weight one on every
  forbidden permutation.
- `benchmark_outcome` states whether the result improves the known
  (P(7,4)\le35) bound.
- `iterations` is the number of cutting-plane LP solves.
- `feasibility_rescale` records a final uniform scaling used to remove any tiny
  numerical PSD violation. A value close to one is expected.

The best tested (P(7,4)) assignment is also copied to
`p74_best_weights.json`. “Best” means best among the weight families actually
run, not globally optimal among every possible weighting.

## Numerical status

The solver uses SciPy/HiGHS with eigenvector cutting planes because no dedicated
SDP package was installed. Each final solution is checked against every full
irreducible block and rescaled if necessary so its numerical block minima are
at least \(-1\).

These are strong numerical experiments, not exact algebraic certificates. A
bound close to an important integer threshold must be reconstructed and proved
exactly before being called a theorem. The current (P(7,4)) results are not
close to such a threshold.

## Questions worth asking

1. How much does each refinement improve ordinary Hoffman?
2. Which partitions are active, and how do they change with the weight family?
3. Do optimized weights show simple rational or algebraic patterns?
4. Is the improvement from shell to shell-plus-cycle-type large enough to
   justify trying displacement-profile or inverse-pair weights?
5. Does the method approach known exact values on validation cases?

Do not claim that failure of these two structured families rules out all
weighted Hoffman certificates. It only shows that these particular weight
spaces did not reach the target.

## Suggested prompt

> Read `RESULTS_GUIDE.md` and `WEIGHTED_SPECTRAL_NEXT_PHASE.md`, then analyze
> `weighted_bounds.json`, `weighted_bounds.csv`, `weighted_report.md`, and
> `p74_best_weights.json`. Compare ordinary, shell, and shell-plus-cycle-type
> bounds. Identify active representations and patterns in the weights. Clearly
> distinguish numerical feasibility, optimization evidence, exact facts, and
> conjectures. State plainly whether the known (P(7,4)\le35) bound improved,
> and do not generalize failure of the tested families to arbitrary weights.
