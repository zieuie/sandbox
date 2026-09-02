# Next Phase: Unrestricted Weighted Hoffman and Fourier-Domain Lovász Theta

## Goal

The structured weighted-Hoffman experiments improved the bounds substantially but did not reach the known benchmark for \(P(7,4)\).

Current numerical results include:

- \(P(5,3)\):
  - ordinary Hoffman: about \(19.42\),
  - shell weights: about \(16.48\),
  - shell + cycle type: about \(12.77\),
  - exact value: \(10\).

- \(P(7,4)\):
  - ordinary Hoffman: about \(642.91\),
  - shell weights: about \(237.78\),
  - shell + cycle type: about \(94.57\),
  - known range:
    \[
    33\le P(7,4)\le35.
    \]

The next goal is to determine whether the limitation comes from the restricted weight families or from the weighted-Hoffman method itself, and then move to the stronger Lovász theta SDP.

---

## 1. Quick diagnostic: unrestricted weighted Hoffman on \(P(5,3)\)

Let

\[
H_{n,d}
=
\operatorname{Cay}(S_n,X),
\qquad
X=\{g:0<\delta_\infty(g,e)<d\}.
\]

Then

\[
P(n,d)=\alpha(H_{n,d}).
\]

For weighted Hoffman, choose a symmetric real weight function

\[
w(g)=w(g^{-1}),
\]

supported on \(X\), and define

\[
W=\sum_{g\in X}w(g)R_g.
\]

Normalize so that every nontrivial irreducible block satisfies

\[
W_\lambda\succeq-I.
\]

Then maximize the row sum

\[
R=\sum_{g\in X}w(g).
\]

The resulting bound is

\[
P(n,d)\le\frac{n!}{1+R}.
\]

### Task

For \(P(5,3)\), use one variable for every inverse pair

\[
\{g,g^{-1}\}\subseteq X.
\]

Do not aggregate by shell, cycle type, or displacement profile.

This is small enough to solve directly.

### Interpretation

The exact value is

\[
P(5,3)=10.
\]

To obtain the integer bound \(10\), it is sufficient to achieve

\[
\frac{120}{1+R}<11,
\]

i.e.

\[
\boxed{
R>\frac{120}{11}-1
\approx9.90909.
}
\]

If unrestricted weighted Hoffman reaches this threshold, the earlier failure was caused mainly by overly coarse weight classes.

If it does not, that is evidence that the weighted ratio bound itself is too weak for this problem.

Save:

```text
results/p53_unrestricted_weighted.json
```

including:

```text
number_of_variables
weights
row_sum
real_bound
integer_bound
active_partitions
minimum_block_eigenvalue
solver_status
```

---

# 2. Main next method: Lovász theta

The forbidden graph is

\[
H_{n,d}
=
\operatorname{Cay}(S_n,X),
\]

with

\[
X=\{g:0<\delta_\infty(g,e)<d\}.
\]

Since a permutation code of minimum distance \(d\) is an independent set,

\[
\boxed{
P(n,d)=\alpha(H_{n,d}).
}
\]

Lovász theta gives

\[
\boxed{
P(n,d)\le\vartheta(H_{n,d}).
}
\]

The important point is that theta for a Cayley graph can be symmetry-reduced using the irreducible representations of the group.

The existing Phase D code already constructs the matrices

\[
\rho^\lambda(g)
\]

for every partition

\[
\lambda\vdash n.
\]

Reuse that machinery.

---

## 3. Fourier-domain theta formulation

Introduce one Hermitian/real-symmetric PSD matrix

\[
A_\lambda\succeq0
\]

for each irreducible representation

\[
\lambda\vdash n.
\]

Use the Fourier-domain Cayley-graph theta SDP:

\[
\begin{aligned}
\text{maximize}\qquad&
A_{(n)}
\\[1mm]
\text{subject to}\qquad&
A_\lambda\succeq0
&&\forall\lambda\vdash n,
\\[1mm]
&
\sum_{\lambda\vdash n}
f^\lambda\operatorname{Tr}(A_\lambda)
=n!,
\\[1mm]
&
\sum_{\lambda\vdash n}
f^\lambda
\left\langle
A_\lambda,\rho^\lambda(x)
\right\rangle
=0
&&\forall x\in X.
\end{aligned}
\]

Here

\[
f^\lambda=\dim V_\lambda.
\]

Check the normalization carefully against the chosen Fourier-transform convention before trusting numerical values.

The optimum should equal

\[
\vartheta(H_{n,d}).
\]

---

# 4. First validation case: \(P(5,3)\)

Compute

\[
\vartheta(H_{5,3}).
\]

The exact code size is

\[
P(5,3)=10.
\]

Questions:

1. Does theta equal \(10\)?
2. If not, how close is it?
3. Which irreducible blocks are active/nonzero at the optimum?
4. Is there a recognizable exact solution?

This is the first correctness and strength test.

---

# 5. Main target: \(P(7,4)\)

Compute

\[
\vartheta(H_{7,4}).
\]

The known benchmark is

\[
33\le P(7,4)\le35.
\]

Interpret the numerical result as follows:

### No improvement

If

\[
\vartheta(H_{7,4})\ge35,
\]

theta does not beat the known upper bound.

### Improved bound

If

\[
34\le\vartheta(H_{7,4})<35,
\]

then

\[
P(7,4)\le34.
\]

### Exact solution

If

\[
33\le\vartheta(H_{7,4})<34,
\]

then

\[
\boxed{
P(7,4)=33.
}
\]

Any result near an integer threshold must be checked at higher precision before making a claim.

---

# 6. Constraint reduction

The naive edge constraints include one equality for every

\[
x\in X.
\]

Before solving a large SDP, merge redundant constraints whenever the Fourier expressions are identical.

Possible reductions:

- inversion:
  \[
  x\leftrightarrow x^{-1};
  \]
- reversal conjugation:
  \[
  x\leftrightarrow w_0xw_0;
  \]
- any additional automorphisms already verified for the Chebyshev metric.

Do not merge by cycle type alone unless the actual Fourier constraints are provably equivalent.

---

# 7. Numerical implementation

Prefer a genuine SDP solver if one is available.

Possible route:

```text
CVXPY + installed SDP solver
```

If no appropriate solver is installed, report that limitation rather than silently replacing theta with another heuristic.

For every solution verify:

```text
all A_lambda are PSD
normalization residual is small
all forbidden-edge equality residuals are small
objective matches the reported theta value
```

Record the maximum constraint violation.

---

# 8. Deliverables

Produce:

```text
results/theta_bounds.json
results/theta_report.md
```

For each case record:

```text
n
d
theta_value
integer_upper_bound
known_code_value_or_range
solver
solver_status
max_constraint_residual
minimum_psd_eigenvalue
nonzero_blocks
active_constraints_summary
```

Also include the unrestricted weighted-Hoffman result for \(P(5,3)\) for comparison.

---

# 9. Experiment order

Run in this order:

```text
1. unrestricted weighted Hoffman for P(5,3)
2. theta for P(5,3)
3. theta for P(7,4)
```

If theta reproduces or nearly reproduces \(P(5,3)=10\), proceed immediately to \(P(7,4)\).

If theta performs poorly even on \(P(5,3)\), report that before investing in larger cases.

---

# 10. Main research question

The decisive question is:

\[
\boxed{
\text{Can the full symmetry-reduced Lovász theta bound see the small
Chebyshev permutation-code numbers that weighted Hoffman misses?}
}
\]

In particular:

\[
\boxed{
\text{Can it prove }P(7,4)<35?
}
\]
