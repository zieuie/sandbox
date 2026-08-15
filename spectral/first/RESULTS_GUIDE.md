# How to read the Phase A--C results

The files contain one record for each pair `(n, d)`. The graph has all `n!`
permutations as vertices. Two permutations are adjacent when their Chebyshev
distance is at least `d`; therefore a clique is a permutation code with minimum
distance at least `d`.

## Core fields

- `r = d - 1` is the radius of the complementary ball.
- `factorial_n` is the number of graph vertices, (n!).
- `ball_size` is 
  \(b_{n,r}=|B_r|=\operatorname{per}(M_r)\).
- `degree = factorial_n - ball_size` is the degree of the distance graph.
- `band_determinant` is \(\det(M_r)\).
- `sign_adjacency_eigenvalue = -band_determinant` is the eigenvalue supplied by
  the sign representation.

`spectrum` lists the distinct adjacency eigenvalues and their full
multiplicities. Values are numerical approximations: treat values within about
`1e-8` as equal, and treat tiny values such as `-6e-16` as zero.

## Standard-representation fields

`standard_ball_eigenvalues` contains the (n-1) eigenvalues of the ball
operator on the standard representation \((n-1,1)\). Their negatives are in
`standard_adjacency_eigenvalues`.

If a listed standard eigenvalue occurs `m` times, its predicted contribution to
the full adjacency spectrum has multiplicity `m*(n-1)`. Its total multiplicity
may be larger because other irreducible representations can have the same
eigenvalue.

Compare the minimum of `standard_adjacency_eigenvalues` with
`least_adjacency_eigenvalue` to test whether the standard representation
supplies the least adjacency eigenvalue. Equality does not prove it is the only
representation supplying that value.

## Extremal and coding fields

- `least_adjacency_eigenvalue` is the minimum eigenvalue of the distance graph.
  Its negative is the largest nontrivial eigenvalue of the ball operator.
- `laplacian_gap` is the second-smallest Laplacian eigenvalue.
- `least_complement_eigenvalue` is the eigenvalue used in Hoffman's bound.
- `least_ball_operator_eigenvalue` equals
  `least_complement_eigenvalue + 1`. This is a *minimum*, distinct from the ball
  eigenvalue obtained by negating `least_adjacency_eigenvalue`.
- `hoffman_clique_bound` is an upper bound on the code/clique size. Subject to
  floating-point tolerance, take its floor to obtain an integer bound. `null`
  means the complement has no negative eigenvalue and this form of Hoffman does
  not apply (not that no clique bound exists).

The extreme cases are useful checks: `d=1` gives the complete graph and `d=n`
gives the edgeless graph.

## What these files can and cannot establish

Useful questions include:

1. For which `(n,d)` does the standard representation attain the least
   adjacency eigenvalue?
2. When does the sign eigenvalue attain it?
3. Are ball sizes, determinants, gaps, or Hoffman bounds following recurrences
   as `n` or `d` changes?
4. How strong is the Hoffman bound relative to `n!` and known code sizes?
5. Which spectra appear integral, allowing for numerical tolerance?

These Phase A--C results do **not** contain exact clique numbers or the complete
irreducible block decomposition. Consequently they cannot identify every
partition responsible for an eigenvalue, prove a pattern from finitely many
rows, or show that a Hoffman bound is sharp without a matching construction.

When reporting a pattern, list the supporting `(n,d)` rows, note all exceptions,
and label it as an observation or conjecture rather than a theorem.

## Suggested prompt

> Read `RESULTS_GUIDE.md` first, then analyze `phase_abc.json` (use the CSV for
> quick tabulation). Check numerical equalities with tolerance `1e-8`. Find
> patterns in ball sizes, spectra, standard/sign eigenvalues, Laplacian gaps,
> and Hoffman clique bounds. Clearly separate verified identities, finite-data
> observations, conjectures, and exceptions. Do not infer unavailable irrep or
> clique-number information.
