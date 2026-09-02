# Next Experiment: Schrijver-Strengthened Theta for Chebyshev Permutation Codes

## Goal

The current Fourier-domain Lovász theta computation gives:

\[
\vartheta(H_{5,3})\approx 10,
\]

matching the known exact value

\[
P(5,3)=10,
\]

and

\[
\vartheta(H_{7,4})\approx 35,
\]

which reproduces but does not improve the known bound

\[
33\le P(7,4)\le35.
\]

The next experiment is to strengthen theta by adding **entrywise nonnegativity**, i.e. compute Schrijver's strengthened theta number

\[
\vartheta'(H_{n,d}).
\]

The main question is:

\[
\boxed{
\text{Does }\vartheta'(H_{7,4})<35?
}
\]

If yes, this improves the known upper bound.

---

# 1. Graph setup

Let

\[
H_{n,d}
=
\operatorname{Cay}(S_n,X),
\]

where

\[
X
=
\{g\in S_n:0<\delta_\infty(g,e)<d\}.
\]

Then a Chebyshev permutation code of minimum distance at least \(d\) is exactly an independent set in \(H_{n,d}\), so

\[
\boxed{
P(n,d)=\alpha(H_{n,d}).
}
\]

---

# 2. Ordinary theta

The existing code computes Lovász theta using the Fourier decomposition over irreducible representations of \(S_n\).

For each partition

\[
\lambda\vdash n,
\]

there is a PSD variable

\[
A_\lambda\succeq0.
\]

Using the current normalization, the SDP has:

\[
\sum_{\lambda\vdash n}
f^\lambda\operatorname{Tr}(A_\lambda)
=
n!,
\]

and for every forbidden difference

\[
x\in X,
\]

\[
\sum_{\lambda\vdash n}
f^\lambda
\left\langle
A_\lambda,\rho^\lambda(x)
\right\rangle
=
0.
\]

The objective is the trivial-block scalar and gives

\[
\vartheta(H_{n,d}).
\]

Reuse the existing implementation exactly; do not rewrite the Fourier machinery unless necessary.

---

# 3. Schrijver strengthening

Schrijver's strengthened theta SDP adds entrywise nonnegativity to the primal theta matrix:

\[
X_{uv}\ge0
\qquad
\forall u,v.
\]

For a Cayley-invariant solution, entries depend only on the relative group element

\[
g=u^{-1}v
\]

or the corresponding convention already used in the code.

Thus the extra condition becomes:

\[
\boxed{
f(g)\ge0
\qquad
\forall g\in S_n,
}
\]

where \(f\) is the positive-type function represented by the Fourier blocks.

Under the current Fourier normalization,

\[
f(g)
=
\frac1{n!}
\sum_{\lambda\vdash n}
f^\lambda
\left\langle
A_\lambda,\rho^\lambda(g)
\right\rangle.
\]

Since the factor \(1/n!\) is positive, it is enough to impose

\[
\boxed{
\sum_{\lambda\vdash n}
f^\lambda
\left\langle
A_\lambda,\rho^\lambda(g)
\right\rangle
\ge0
\qquad
\forall g\in S_n.
}
\]

These are linear inequalities in the SDP variables.

The PSD constraints remain unchanged.

---

# 4. Important implementation check

Before running large cases, verify that the Fourier reconstruction really agrees with the primal entry values under the current representation/action convention.

For a small case such as \(S_3\) or \(S_4\):

1. generate a feasible theta solution;
2. reconstruct
   \[
   f(g)
   \]
   from the Fourier blocks;
3. reconstruct the corresponding invariant primal matrix;
4. verify that its entries agree with the expected relative-element formula.

Do not trust the Schrijver inequalities until this normalization/convention check passes.

---

# 5. Reduce redundant nonnegativity constraints

Naively, add one inequality for every

\[
g\in S_n.
\]

For \(n=7\), this is only

\[
5040
\]

constraints, which may already be manageable.

Still, merge constraints when they are provably identical.

Safe reductions may include:

### Inversion

If the reconstructed invariant matrix is symmetric,

\[
f(g)=f(g^{-1}),
\]

so only one representative of each inverse pair is needed.

### Reversal conjugation

If already verified as a graph/operator symmetry,

\[
g\mapsto w_0gw_0
\]

may also produce redundant constraints.

Do **not** merge merely by cycle type, because Chebyshev distance is not conjugacy invariant.

Start with the simplest correct implementation, even if it includes all \(n!\) inequalities.

---

# 6. First validation case: \(P(5,3)\)

Compute

\[
\vartheta'(H_{5,3}).
\]

Since

\[
P(5,3)=10
\]

and ordinary theta already gives \(10\), the strengthened bound should also satisfy

\[
\boxed{
\vartheta'(H_{5,3})=10
}
\]

up to numerical precision.

If the result is significantly below \(10\), the formulation or normalization is wrong, since theta-prime is still an upper bound on the independence number.

If the result is significantly above \(10\), the strengthening has probably not been applied correctly.

Use this as a strict validation test.

---

# 7. Main target: \(P(7,4)\)

Compute

\[
\vartheta'(H_{7,4}).
\]

The known range is

\[
33\le P(7,4)\le35.
\]

Interpret the result carefully.

### If

\[
\vartheta'(H_{7,4})\ge35,
\]

then this strengthening does not improve the known upper bound.

### If

\[
34\le\vartheta'(H_{7,4})<35,
\]

then

\[
\boxed{
P(7,4)\le34.
}
\]

### If

\[
33\le\vartheta'(H_{7,4})<34,
\]

then

\[
\boxed{
P(7,4)=33.
}
\]

Any value near \(34\) or \(35\) must be rerun at higher precision and preferably certified before claiming a new integer bound.

---

# 8. Numerical diagnostics

For every run record:

```text
theta_prime_value
integer_upper_bound
solver
solver_status
normalization_residual
max_forbidden_edge_residual
minimum_psd_eigenvalue
minimum_reconstructed_f_value
max_nonnegativity_violation
number_of_nonnegativity_constraints
nonzero_irrep_blocks
```

The crucial feasibility checks are:

\[
A_\lambda\succeq0
\]

for every \(\lambda\),

\[
f(x)=0
\]

for every forbidden

\[
x\in X,
\]

and

\[
f(g)\ge0
\]

for every

\[
g\in S_n.
\]

---

# 9. Compare against ordinary theta

Produce a small table:

```text
case      ordinary theta      theta-prime      known value/range
P(5,3)    ~10                 ?                10
P(7,4)    ~35                 ?                33..35
```

The important quantity is the drop

\[
\vartheta(H)-\vartheta'(H).
\]

If theta-prime remains \(35\) for \(P(7,4)\), that is evidence that this entire one-matrix theta family is insufficient to beat the anticode bound.

---

# 10. Optional exact-certificate side task for \(P(5,3)\)

The unrestricted weighted-Hoffman solution for \(P(5,3)\) produced weights numerically very close to

\[
0,\quad\frac13,\quad\frac23
\]

with row sum very close to

\[
11.
\]

If convenient, round the numerical weights to these values and test the resulting Fourier blocks exactly or at high precision.

If all nontrivial blocks satisfy

\[
W_\lambda\succeq-I
\]

exactly, this gives a clean explicit certificate:

\[
P(5,3)
\le
\frac{120}{1+11}
=
10.
\]

This is secondary to the theta-prime experiment, but worth preserving because it may reveal useful combinatorial structure.

---

# 11. Deliverables

Produce:

```text
results/theta_prime_bounds.json
results/theta_prime_report.md
```

For \(P(7,4)\), also save enough information to reproduce the optimum:

```text
results/p74_theta_prime_solution.json
```

including:

```text
objective
solver
solver_status
all feasibility diagnostics
nonzero block summaries
minimum f(g)
groups/elements attaining minimum f(g)
```

---

# 12. Experiment order

Run:

```text
1. normalization/convention sanity check on a tiny S_n
2. theta-prime for P(5,3)
3. theta-prime for P(7,4)
```

Do not move to larger cases until these are understood.

---

# 13. Main research question

\[
\boxed{
\text{Does entrywise nonnegativity strengthen the Fourier theta bound enough to prove }
P(7,4)<35?
}
\]

If not, the next step should be a genuinely higher-order SDP rather than further tuning of weighted Hoffman.
