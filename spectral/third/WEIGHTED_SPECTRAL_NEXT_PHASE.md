# Next Experiment: Weighted Spectral Bounds for Chebyshev Permutation Codes

## Goal

Use the existing irreducible \(S_n\)-block machinery to test whether **optimized weighted spectral bounds** can improve known upper bounds for Chebyshev permutation codes.

The first serious target is

\[
\boxed{33\le P(7,4)\le 35.}
\]

The immediate success criterion is simple:

- if the method proves \(P(7,4)\le 34\), it improves the known upper bound;
- if it proves \(P(7,4)\le 33\), then it proves
  \[
  \boxed{P(7,4)=33.}
  \]

Do not begin by extending unweighted full spectra to larger \(n\). The next question is whether the representation machinery can produce a **better certificate** than the ordinary \(0/1\) adjacency matrix.

---

## 1. Coding problem as an independent-set problem

Let

\[
P(n,d)
=
\max\left\{
|C|:
C\subseteq S_n,\ 
\delta_\infty(\sigma,\tau)\ge d
\text{ for all distinct }\sigma,\tau\in C
\right\}.
\]

Define the forbidden-distance graph \(H_{n,d}\) on \(S_n\) by

\[
\sigma\sim\tau
\iff
0<\delta_\infty(\sigma,\tau)<d.
\]

Then

\[
\boxed{P(n,d)=\alpha(H_{n,d}).}
\]

The ordinary Hoffman calculation uses the unweighted adjacency operator

\[
A
=
\sum_{\substack{g\in S_n\\0<\delta_\infty(g,e)<d}}R_g.
\]

There is no reason the coefficient \(1\) on every forbidden difference should be optimal.

---

## 2. Replace the adjacency matrix by a weighted one

Choose a real symmetric weight function

\[
w(g)=w(g^{-1})
\]

such that

\[
w(e)=0
\]

and

\[
w(g)=0
\qquad
\text{whenever }\delta_\infty(g,e)\ge d.
\]

Thus weights are supported only on forbidden relative permutations.

Define

\[
W
=
\sum_{g\in S_n}w(g)R_g.
\]

Every valid code still has zero off-diagonal entries in its principal submatrix of \(W\), because codeword differences have distance at least \(d\).

The row sum is

\[
R=\sum_g w(g).
\]

Let the least eigenvalue of \(W\) be

\[
\tau<0.
\]

The weighted Hoffman/ratio bound gives

\[
\boxed{
P(n,d)
\le
n!\frac{-\tau}{R-\tau}.
}
\]

Scaling all weights does not change the bound.

Therefore normalize

\[
\tau\ge -1.
\]

Then maximize \(R\). At optimum,

\[
\boxed{
P(n,d)\le \frac{n!}{1+R}.
}
\]

---

## 3. Representation-theoretic reduction

The existing Phase D machinery already block-diagonalizes group-algebra operators.

For each irreducible representation

\[
\lambda\vdash n,
\]

define

\[
W_\lambda
=
\sum_gw(g)\rho^\lambda(g).
\]

The condition

\[
\lambda_{\min}(W)\ge -1
\]

is equivalent to

\[
\boxed{
W_\lambda\succeq -I
\qquad
\text{for every nontrivial }\lambda\vdash n.
}
\]

Thus choosing optimal weights becomes an SDP:

\[
\begin{aligned}
\text{maximize}\qquad&
R=\sum_gw(g)\\
\text{subject to}\qquad&
\sum_gw(g)\rho^\lambda(g)\succeq-I
&&
\forall\lambda\ne(n),\\
&w(g)=w(g^{-1}),\\
&w(g)=0
&&
\text{if }\delta_\infty(g,e)\ge d,\\
&w(e)=0.
\end{aligned}
\]

The trivial block is not constrained by \(-I\); its scalar value is precisely the row sum \(R\).

This is the main new experiment.

---

# 4. Do not begin with one variable per permutation

Start with small structured weight spaces and increase complexity only if necessary.

For each weight family, solve the SDP and record the resulting upper bound.

---

## Stage 1: shell weights

Give the same weight to all relative permutations with the same Chebyshev displacement.

Let

\[
S_j
=
\{g\in S_n:\delta_\infty(g,e)=j\}.
\]

Use variables

\[
a_1,\ldots,a_{d-1}
\]

and set

\[
w(g)=a_j
\qquad(g\in S_j).
\]

Then

\[
W_\lambda
=
\sum_{j=1}^{d-1}a_j S_{j,\lambda},
\]

where

\[
S_{j,\lambda}
=
\sum_{g\in S_j}\rho^\lambda(g).
\]

These shell blocks can be obtained cheaply from the already implemented ball blocks:

\[
S_{j,\lambda}
=
T^{(j)}_\lambda-T^{(j-1)}_\lambda,
\]

where

\[
T^{(r)}_\lambda
=
\sum_{\delta_\infty(g,e)\le r}\rho^\lambda(g).
\]

For \(P(7,4)\), Stage 1 has only three variables:

\[
a_1,a_2,a_3.
\]

This should be the first experiment.

---

## Stage 2: shell + cycle type

If shell weights do not beat the known bound, refine the weight classes.

Use one variable for each pair

\[
\bigl(
\delta_\infty(g,e),
\operatorname{cycle\_type}(g)
\bigr).
\]

That is, permutations receive the same weight iff they have both:

1. the same Chebyshev displacement, and
2. the same cycle type.

This is still a fairly small parameter space for \(S_7\).

Important: these classes are generally **not conjugacy classes**, because Chebyshev displacement is not conjugacy invariant. That is fine. The existing non-normal Cayley representation machinery still applies.

---

## Stage 3: displacement-profile weights

If necessary, distinguish permutations using a positional displacement profile, for example

\[
c_j(g)
=
\#\{i:|g(i)-i|=j\}.
\]

A weight class might be indexed by

\[
\left(
\delta_\infty(g,e),
c_1(g),c_2(g),\ldots,c_{d-1}(g)
\right).
\]

This better captures the actual geometry of the Chebyshev ball.

Again enforce inversion symmetry by merging any profile classes that are exchanged by

\[
g\mapsto g^{-1}.
\]

---

## Stage 4: arbitrary inverse-pair weights

Only if the structured families fail and \(n=7\) remains computationally manageable, allow one variable per inverse pair

\[
\{g,g^{-1}\}
\]

among forbidden relative permutations.

This is the most flexible weighted-Hoffman certificate in the group-algebra framework.

Do not start here.

---

# 5. Primary target: \(P(7,4)\)

The currently relevant benchmark is

\[
\boxed{33\le P(7,4)\le35.}
\]

The known upper bound \(35\) comes from an optimal code-anticode argument, so merely reproducing \(35\) is not interesting.

For every weight family report:

```text
weight_family
number_of_variables
optimal_row_sum_R
spectral_upper_bound_real
spectral_upper_bound_floor
active_irrep_constraints
solver_status
```

The key thresholds are:

### Improvement

To prove

\[
P(7,4)\le34,
\]

we need

\[
\frac{7!}{1+R}<35,
\]

equivalently

\[
R>143.
\]

### Exact solution

To prove

\[
P(7,4)\le33,
\]

we need

\[
\frac{7!}{1+R}<34,
\]

equivalently

\[
\boxed{
R>\frac{5040}{34}-1
\approx147.235294.
}
\]

If the computed real-valued bound is numerically extremely close to an integer threshold, rerun with higher precision before claiming an integer improvement.

---

# 6. Validation cases before trusting \(P(7,4)\)

Run the same weighted optimization first on small cases whose exact values are already known.

Suggested sequence:

```text
P(5,3) = 10
P(6,3) = 20
P(7,4): 33 <= P <= 35
P(7,3): 100 <= P <= 105
P(8,4) = 70
```

The purpose is not merely testing code correctness.

Ask:

> Does weighted Hoffman actually recover or approach known sharp values?

For each case, compare:

```text
ordinary Hoffman bound
shell-weight SDP bound
shell+cycle-type SDP bound
richer-weight SDP bound
known best upper bound
known exact value, if known
```

---

# 7. Solver implementation

Use an SDP-capable package already available in the environment if possible, e.g.

```text
CVXPY
```

with an installed solver supporting positive-semidefinite constraints.

The variables are real.

Each nontrivial irreducible block contributes the LMI

\[
W_\lambda+I\succeq0.
\]

Because Young's orthogonal representations are real, the blocks should be real symmetric up to numerical noise.

Explicitly symmetrize if necessary:

```python
W = (W + W.T) / 2
```

before passing it to the solver.

---

# 8. Sanity checks

For every solution verify numerically:

\[
W_\lambda+I\succeq0
\]

for every nontrivial partition.

Record the smallest eigenvalue among all blocks.

It should satisfy

\[
\lambda_{\min}\ge -1-\varepsilon.
\]

Also verify:

\[
R=\sum_gw(g)
\]

directly from the weight classes and class sizes.

The trivial representation block should equal \(R\).

Check inversion symmetry explicitly.

---

# 9. Identify the active representations

One important mathematical output is not merely the numerical bound but **which irreducible constraints are tight**.

For each optimum, report every partition satisfying approximately

\[
\lambda_{\min}(W_\lambda)=-1.
\]

These are the representations controlling the certificate.

This may suggest an exact symbolic weighting.

For example, if only a small collection of partitions is active, try solving the corresponding equalities exactly after obtaining the numerical SDP solution.

---

# 10. Try to recover exact coefficients

If a promising optimum produces weights numerically close to simple rational or quadratic values, use:

- rational reconstruction,
- symbolic algebra,
- exact characteristic polynomials,

to guess exact weights.

Then verify the PSD inequalities exactly or with rigorous algebraic-number arithmetic.

The desired endpoint is not:

> “CVXPY numerically says the bound is 33.000001.”

The desired endpoint is something like:

> Choose weights
> \[
> a_1=\cdots,\quad a_2=\cdots,\quad a_3=\cdots.
> \]
> Their row sum is \(R\), and every nontrivial Fourier block is bounded below by \(-I\). Therefore
> \[
> P(7,4)\le33.
> \]

A short exact certificate would be a real theorem.

---

# 11. If the weighted ratio bound stalls

If increasingly rich weighted-Hoffman families fail to beat \(35\) for \(P(7,4)\), record that clearly.

Then the natural next escalation is a symmetry-reduced Lovász-theta / SDP bound rather than simply adding more brute-force spectral data.

The existing irreducible decomposition should still be useful there.

Do not implement full Lovász theta until the weighted ratio-bound experiment has been exhausted enough to tell whether this cheaper method has any strength.

---

# 12. Deliverables

Produce:

```text
results/weighted_bounds.csv
results/weighted_bounds.json
results/weighted_report.md
```

and save the best weight assignment for each test case.

For \(P(7,4)\), additionally save:

```text
results/p74_best_weights.json
```

containing:

```text
weight_family
weight_classes
weights
row_sum
real_bound
integer_bound
active_partitions
minimum_block_eigenvalue
solver
solver_tolerance
```

---

# 13. First concrete task

Implement **Stage 1 shell weighting** using the existing Phase D block code.

Run it on:

```text
(n,d) = (5,3)
(n,d) = (7,4)
```

For \(P(7,4)\), solve

\[
\max_{a_1,a_2,a_3} R
\]

subject to

\[
a_1S_{1,\lambda}
+
a_2S_{2,\lambda}
+
a_3S_{3,\lambda}
\succeq-I
\]

for every nontrivial

\[
\lambda\vdash7.
\]

Report:

1. optimal \(a_1,a_2,a_3\);
2. optimal row sum \(R\);
3. resulting Hoffman bound;
4. every active partition;
5. whether the integer bound is:
   - \(35\) or worse,
   - \(34\),
   - or \(33\).

If shell weights do not improve \(35\), proceed immediately to Stage 2.

---

# 14. Research question

The practical question for this phase is:

\[
\boxed{
\text{Can a symmetry-aware weighted spectral certificate prove }
P(7,4)<35?
}
\]

Everything else is secondary until we know the answer.
