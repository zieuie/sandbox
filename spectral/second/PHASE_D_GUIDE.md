# Phase D Brief: Irreducible Spectral Decomposition of Chebyshev-Distance Graphs on \(S_n\)

## Purpose

This file is the handoff for the next phase of the spectral experiments.

The existing Phase A--C work computed full adjacency spectra for small cases, together with:

- Chebyshev balls \(B_r\),
- band matrices \(M_r\),
- ball sizes \(|B_r|=\operatorname{per}(M_r)\),
- sign-representation eigenvalues,
- the standard-representation block obtained from the matrix \(Q\),
- least adjacency eigenvalues,
- least complement/ball eigenvalues,
- Laplacian gaps,
- Hoffman clique bounds.

Read these existing files first:

```text
RESULTS_GUIDE.md
phase_abc.json
phase_abc.csv
```

Treat those files as the source of truth for the Phase A--C numerical results.

The next goal is **not** merely to extend the brute-force full graph calculations. The main task is to decompose the relevant convolution operator into irreducible \(S_n\)-blocks, identify which partitions produce the extremal eigenvalues, and push to values of \(n\) where the full \(n!\times n!\) adjacency matrix is no longer practical.

---

# 1. Mathematical setup

Let

\[
G_{n,d}
\]

have vertex set \(S_n\), with

\[
\sigma\sim\tau
\iff
\delta_\infty(\sigma,\tau)\ge d,
\]

where

\[
\delta_\infty(\sigma,\tau)
=
\max_i |\sigma(i)-\tau(i)|.
\]

Set

\[
r=d-1.
\]

Define the Chebyshev ball around the identity

\[
B_r
=
\left\{
\pi\in S_n:
|\pi(i)-i|\le r\text{ for all }i
\right\}.
\]

Let

\[
b=|B_r|.
\]

The graph is a Cayley graph. The useful operator for representation theory is the **ball operator**

\[
T
=
\sum_{\pi\in B_r}R_\pi,
\]

where \(R_\pi\) denotes the chosen regular action. Use one left/right convention consistently throughout the code.

For an irreducible representation \(V_\lambda\), \(\lambda\vdash n\), define

\[
T_\lambda
=
\sum_{\pi\in B_r}\rho^\lambda(\pi).
\]

Because \(B_r=B_r^{-1}\), \(T_\lambda\) may be represented as a real symmetric or Hermitian matrix when a unitary/orthogonal realization of \(\rho^\lambda\) is used.

---

# 2. Relation between the ball operator and the graph spectrum

This distinction is essential.

Let \(A\) be the adjacency matrix of \(G_{n,d}\).

The full group sum acts by \(n!\) on the trivial representation and by \(0\) on every nontrivial irreducible representation.

Therefore:

## Trivial block

\[
\lambda_{\mathrm{trivial}}(A)
=
n!-b.
\]

This is the graph degree.

## Nontrivial block

For every \(\lambda\ne(n)\),

\[
A|_{V_\lambda}
=
-T_\lambda.
\]

Thus an eigenvalue

\[
\mu\in\operatorname{Spec}(T_\lambda)
\]

produces the adjacency eigenvalue

\[
-\mu.
\]

In the full regular representation, each eigenvalue of \(T_\lambda\) is repeated an additional factor

\[
f^\lambda=\dim V_\lambda.
\]

So if \(\mu\) has multiplicity \(m\) inside \(T_\lambda\), then \(-\mu\) occurs in the full adjacency spectrum with multiplicity

\[
m f^\lambda.
\]

---

# 3. Two different extremal questions

Do **not** conflate these.

The Phase A--C data suggests that the two extremal directions are controlled by different representations.

## Question A: least adjacency eigenvalue

Since nontrivial adjacency eigenvalues are \(-\mu\),

\[
\lambda_{\min}(A)
=
-
\max_{\lambda\ne(n)}
\lambda_{\max}(T_\lambda).
\]

The existing data strongly suggests the following conjecture:

> **Standard-representation conjecture.**
> For every nontrivial tested case,
> \[
> \max_{\lambda\ne(n)}\lambda_{\max}(T_\lambda)
> =
> \lambda_{\max}(T_{(n-1,1)}).
> \]
> Equivalently,
> \[
> \lambda_{\min}(A)
> =
> -
> \lambda_{\max}(T_{(n-1,1)}).
> \]

This is only a finite-data observation at present.

The next phase should test this systematically and identify any exceptions.

---

## Question B: Hoffman-relevant eigenvalue

The complement of \(G_{n,d}\) has adjacency operator

\[
T-I.
\]

Therefore, for a nontrivial block,

\[
A_{\overline G}|_{V_\lambda}
=
T_\lambda-I.
\]

The least complement eigenvalue is therefore controlled by

\[
\mu_{\min}
=
\min_{\lambda\ne(n)}
\lambda_{\min}(T_\lambda).
\]

The least complement eigenvalue is

\[
\tau
=
\mu_{\min}-1.
\]

This is the quantity used in Hoffman's bound for the clique number of \(G_{n,d}\).

The data already shows that the standard representation often does **not** attain \(\mu_{\min}\).

For example, in the existing \(n=5,d=3\) result:

\[
\lambda_{\min}(T_{(4,1)})
\approx -4.42686044,
\]

while the global ball-operator minimum is approximately

\[
-4.79128785.
\]

Therefore some other irreducible representation supplies the Hoffman-relevant eigenvalue.

This is arguably the more important extremal problem for permutation-code bounds.

---

# 4. Primary Phase D objective

For each tested \((n,d)\) and every partition

\[
\lambda\vdash n,
\]

compute:

\[
T_\lambda
=
\sum_{\pi\in B_{d-1}}\rho^\lambda(\pi),
\]

and record:

```text
partition
dimension
block_min_eigenvalue
block_max_eigenvalue
block_trace
block_eigenvalues
```

Then identify separately:

```text
partition_maximizing_block_max
partition_minimizing_block_min
```

These answer the two different extremal questions above.

The output should make it impossible to accidentally confuse them.

---

# 5. Verification against Phase A--C

Before trusting any larger-\(n\) result, reproduce the known full spectra for the cases already present in `phase_abc.json`.

The current Phase A--C dataset contains:

```text
n = 3, 4, 5
d = 1, ..., n
```

For each of those cases, assemble the full representation-theoretic spectrum from the irreducible blocks and verify that it agrees with the stored direct spectrum to tolerance `1e-8`.

Required checks:

\[
\sum_{\lambda\vdash n}(f^\lambda)^2=n!.
\]

For every block:

\[
T_\lambda=T_\lambda^\ast
\]

within numerical tolerance.

For the full reconstructed adjacency spectrum:

\[
\sum_i \lambda_i=0,
\]

and

\[
\sum_i\lambda_i^2=n!\,(n!-b).
\]

The reconstructed spectrum must match the direct `spectrum` field from Phase A--C, including multiplicities.

Do not proceed to conjecture hunting until these checks pass.

---

# 6. Preferred implementation strategy

First inspect the available environment.

Prefer, in this order:

1. **SageMath** symmetric-group representations if available.
2. **GAP via SageMath** if convenient.
3. A direct implementation of Young's orthogonal/seminormal representation if necessary.

Do not build the full regular representation.

The entire point is to work blockwise.

A useful interface is:

```python
partitions_of_n(n)
irrep_dimension(lam)
irrep_generators(lam)
irrep_matrix(lam, perm)
ball_block(n, d, lam)
block_spectrum(n, d, lam)
```

Cache aggressively.

---

# 7. Direct Young/seminormal implementation if needed

If no suitable representation library is available, use standard Young tableaux.

For each partition \(\lambda\):

- enumerate standard Young tableaux of shape \(\lambda\);
- use them as a basis for \(V_\lambda\);
- implement the adjacent transpositions
  \[
  s_i=(i\,\,i+1);
  \]
- express arbitrary permutations as products of adjacent transpositions;
- multiply the generator matrices.

Use an orthogonal/seminormal form so that the matrices are real orthogonal and the final block \(T_\lambda\) is symmetric.

Before using this implementation for experiments, verify the Coxeter relations:

\[
s_i^2=I,
\]

\[
s_is_j=s_js_i
\qquad
(|i-j|>1),
\]

and

\[
s_is_{i+1}s_i=s_{i+1}s_is_{i+1}.
\]

Also verify character traces against independently computed irreducible characters for small \(n\), if possible.

---

# 8. Generate \(B_r\) directly

Do not enumerate all of \(S_n\) and filter once \(n\) becomes large.

Generate only permutations satisfying

\[
|\pi(i)-i|\le r.
\]

A simple backtracking generator is sufficient initially.

Possible improvement:

- at each recursion step, choose the unassigned position with the fewest available values;
- prune immediately if Hall-type feasibility fails;
- cache recurring subproblems if useful.

For fixed small \(r\), \(|B_r|\) can be dramatically smaller than \(n!\).

---

# 9. Numerical representation of block spectra

Because \(T_\lambda\) is Hermitian/symmetric, use a symmetric eigensolver:

```python
numpy.linalg.eigvalsh
scipy.linalg.eigh
```

Avoid generic complex eigensolvers unless necessary.

For small blocks, consider computing exact characteristic polynomials with SageMath to recognize algebraic eigenvalues.

At minimum, record floating-point values with enough digits to distinguish nearby eigenvalues.

Use tolerance:

```text
1e-8
```

for Phase A--C comparisons.

---

# 10. Output schema

Create a machine-readable file such as:

```text
results/phase_d_blocks.json
```

with one record per \((n,d)\), containing:

```json
{
  "n": 6,
  "d": 3,
  "r": 2,
  "ball_size": 0,
  "partitions": [
    {
      "partition": [6],
      "dimension": 1,
      "block_min": 0.0,
      "block_max": 0.0,
      "block_trace": 0.0,
      "eigenvalues": []
    }
  ],
  "max_nontrivial_block_max": 0.0,
  "maximizing_partitions": [],
  "min_nontrivial_block_min": 0.0,
  "minimizing_partitions": [],
  "standard_block_min": 0.0,
  "standard_block_max": 0.0,
  "hoffman_bound": 0.0
}
```

The zeros above are placeholders, not expected values.

Also produce a CSV summary with:

```text
n
d
ball_size
standard_block_min
standard_block_max
global_nontrivial_block_min
global_nontrivial_block_max
minimizing_partition
maximizing_partition
least_adjacency_eigenvalue
hoffman_clique_bound
```

If several partitions tie, record all of them.

---

# 11. Immediate experiment grid

First reproduce:

```text
n = 3, 4, 5
d = 1, ..., n
```

exactly.

Then attempt:

```text
n = 6, 7, 8
d = 2, ..., n-1
```

Do not insist on completing every \((n,d)\) if a particular ball/block calculation becomes disproportionately expensive.

The priority is to collect enough exact block information to answer:

1. Does \((n-1,1)\) always maximize \(\lambda_{\max}(T_\lambda)\)?
2. Which partitions minimize \(\lambda_{\min}(T_\lambda)\)?
3. Is there a recognizable pattern in those minimizing partitions as \(d\) varies?

---

# 12. Special analytic track I: \(d=2\)

This case has additional combinatorial structure and should not be treated as generic brute force.

Here

\[
r=1.
\]

A permutation belongs to \(B_1\) iff it is a product of disjoint adjacent transpositions.

Thus \(B_1\) is naturally identified with the matchings of the path \(P_n\).

Therefore:

\[
|B_1|=F_{n+1},
\]

where

\[
F_1=F_2=1.
\]

This explains the observed ball sizes:

\[
3,5,8
\]

for \(n=3,4,5\).

---

## 12.1 Exact standard-representation matrix for \(d=2\)

Recall

\[
Q_{ij}
=
\#\{\pi\in B_1:\pi(i)=j\}.
\]

The matching interpretation gives:

\[
Q_{ii}
=
F_iF_{n-i+1},
\]

\[
Q_{i,i+1}
=
Q_{i+1,i}
=
F_iF_{n-i},
\]

and

\[
Q_{ij}=0
\qquad
(|i-j|>1).
\]

Thus \(Q\) is a symmetric tridiagonal matrix with explicit Fibonacci entries.

Its row sums are

\[
F_{n+1}.
\]

The trivial eigenvalue is therefore

\[
F_{n+1},
\]

and the other \(n-1\) eigenvalues are exactly the eigenvalues of

\[
T_{(n-1,1)}.
\]

### Task

Implement this formula independently of permutation enumeration and verify it against the existing Phase A--C standard eigenvalues.

Then push it to much larger \(n\).

Investigate:

- characteristic polynomials,
- recurrences,
- largest nontrivial eigenvalue,
- smallest eigenvalue,
- asymptotics,
- possible closed forms after exploiting reversal symmetry.

---

## 12.2 Determinant for \(d=2\)

The band matrix \(M_1\) is tridiagonal with ones on the main, upper, and lower diagonals.

Let

\[
D_n=\det(M_1).
\]

Then

\[
D_n=D_{n-1}-D_{n-2},
\]

with

\[
D_1=1,\qquad D_2=0.
\]

Hence \(D_n\) is periodic of period \(6\):

\[
1,0,-1,-1,0,1,\ldots
\]

and the sign adjacency eigenvalue is

\[
-D_n.
\]

Verify this exactly.

---

# 13. Special analytic track II: \(d=n-1\)

This case also has very low-complexity structure.

The maximum possible displacement is \(n-1\).

Therefore a relative permutation lies in the connection set iff

\[
\pi(1)=n
\]

or

\[
\pi(n)=1.
\]

Hence

\[
C
=
\{\pi:\pi(1)=n\}
\cup
\{\pi:\pi(n)=1\}.
\]

By inclusion-exclusion,

\[
|C|
=
2(n-1)!-(n-2)!
=
(2n-3)(n-2)!.
\]

Thus the graph degree is

\[
k=(2n-3)(n-2)!.
\]

This agrees with the existing small cases.

---

# 14. Candidate exact spectrum for \(d=n-1\)

The Phase A--C spectra for \(n=4,5\) strongly fit the following formula.

Treat this as a **theorem target to prove**, not as an established repository theorem.

For \(n\ge4\), the candidate adjacency spectrum is:

\[
\boxed{
\begin{array}{c|c}
\text{eigenvalue}&\text{multiplicity}\\
\hline
(2n-3)(n-2)! &1\\[2mm]
-(n-1)! &n-1\\[2mm]
(n-3)(n-2)! &n-1\\[2mm]
-(n-2)! &\dfrac{n(n-3)}2\\[3mm]
+(n-2)! &\dfrac{(n-1)(n-2)}2\\[3mm]
0&n!-n(n-1)
\end{array}
}
\]

When numerical eigenvalues coincide, combine their multiplicities.

### Checks against existing data

For \(n=5\), this predicts:

```text
42       multiplicity 1
-24      multiplicity 4
12       multiplicity 4
-6       multiplicity 5
6        multiplicity 6
0        multiplicity 100
```

which matches the stored Phase A--C spectrum.

For \(n=4\), the two positive values

\[
(n-3)(n-2)!
\quad\text{and}\quad
(n-2)!
\]

both equal \(2\), so their multiplicities combine.

### Task

Derive this formula rigorously from the coset structure of

\[
\{\pi:\pi(1)=n\}
\quad\text{and}\quad
\{\pi:\pi(n)=1\}.
\]

Possible approaches:

- factor the adjacency operator through permutation modules on ordered pairs;
- use induced representations from point stabilizers;
- write the operator as a combination of low-rank incidence operators;
- identify which irreducibles survive.

Do not simply fit the formula numerically.

---

# 15. Expected relevant irreducibles in the \(d=n-1\) case

The candidate spectrum suggests that only a small set of partitions should support nonzero action:

\[
(n),
\]

\[
(n-1,1),
\]

\[
(n-2,2),
\]

\[
(n-2,1,1),
\]

with most other irreducible components annihilated.

This is a working hypothesis.

Use the full Phase D block decomposition to verify exactly which partitions have nonzero \(T_\lambda\).

If this is correct, explain it representation-theoretically rather than merely reporting zero matrices.

---

# 16. Candidate Hoffman formula for \(d=n-1\)

If the candidate exact spectrum above is proved, then for \(n\ge5\) the minimum nontrivial ball eigenvalue is

\[
\mu_{\min}
=
-(n-3)(n-2)!.
\]

The Hoffman clique bound would then become

\[
\boxed{
\omega(G_{n,n-1})
\le
\frac{n-1}{n-2}
\left(
(n-3)(n-2)!+1
\right).
}
\]

For \(n=5\), this equals

\[
\frac{52}{3}
=
17.333\ldots,
\]

matching the existing numerical result.

Again, prove the spectral premise before promoting this from a candidate formula to a theorem.

---

# 17. A useful representation-theoretic diagnostic

For every block,

\[
\operatorname{tr}(T_\lambda)
=
\sum_{\pi\in B_r}\chi^\lambda(\pi).
\]

This is the \(\chi^\lambda\)-immanant of the band matrix \(M_r\).

Use this as an independent check of the computed block trace.

Special cases:

- trivial representation:
  \[
  \operatorname{tr}(T_{(n)})=|B_r|=\operatorname{per}(M_r);
  \]
- sign representation:
  \[
  \operatorname{tr}(T_{(1^n)})=\det(M_r).
  \]

For higher-dimensional blocks, character sums will not determine the full spectrum, but they are excellent correctness checks.

---

# 18. Symmetry to exploit

The ball \(B_r\) is preserved by reversal conjugation.

Let

\[
w_0(i)=n+1-i.
\]

Then

\[
\pi\in B_r
\iff
w_0\pi w_0\in B_r.
\]

This implies additional commuting symmetry in each block.

Investigate whether the action of \(w_0\) can be used to decompose \(T_\lambda\) further into \(+1\) and \(-1\) eigenspaces or otherwise reduce computation.

For the standard \(Q\)-matrix, this appears as centrosymmetry:

\[
Q_{ij}
=
Q_{n+1-i,n+1-j}.
\]

Exploit this explicitly in the \(d=2\) analysis.

---

# 19. What not to assume

Do not assume:

- \(B_r\) is a union of conjugacy classes;
- \(T_\lambda\) is scalar;
- characters determine the full block spectrum;
- the standard representation controls the Hoffman bound;
- a finite numerical pattern is a theorem;
- a Hoffman bound is sharp without a matching construction.

The Chebyshev condition depends on actual positions/values, not merely cycle type, so the Cayley graph is generally not normal.

---

# 20. Suggested code organization

Extend the repository with something like:

```text
src/
├── irreps.py
├── tableaux.py
├── ball_blocks.py
├── block_spectrum.py
└── representation_checks.py

experiments/
├── phase_d_verify_small.py
├── phase_d_scan.py
├── d2_fibonacci_standard.py
└── d_nminus1_exact.py

results/
├── phase_d_blocks.json
├── phase_d_summary.csv
└── phase_d_report.md
```

Keep the special cases separate from the generic block code.

---

# 21. Required report

Produce:

```text
results/phase_d_report.md
```

with four clearly separated categories:

## Verified identities

Things checked algebraically or against all available direct spectra.

## Finite-data observations

Patterns seen in the computed \((n,d)\) range.

## Conjectures

In particular:

\[
\max_{\lambda\ne(n)}
\lambda_{\max}(T_\lambda)
=
\lambda_{\max}(T_{(n-1,1)}).
\]

## Proven special-case results

Anything rigorously derived for:

- \(d=2\),
- \(d=n-1\).

List all exceptions explicitly.

---

# 22. First concrete task

Start by implementing the complete irreducible block decomposition for

```text
n = 3, 4, 5.
```

For every \(d\):

1. generate \(B_{d-1}\);
2. build every \(T_\lambda\);
3. diagonalize each block;
4. reconstruct the full adjacency spectrum with correct regular-representation multiplicities;
5. compare it with `phase_abc.json`;
6. identify:
   - the partition(s) maximizing \(\lambda_{\max}(T_\lambda)\),
   - the partition(s) minimizing \(\lambda_{\min}(T_\lambda)\);
7. verify the standard block against the existing \(Q\)-matrix data;
8. verify the sign block against \(\det(M_r)\).

Only after all of these checks pass should the scan move to \(n=6,7,8\).

---

# 23. Main research questions

The next phase should ultimately answer as much of the following as possible.

### Question 1

Is it always true that

\[
\lambda_{\min}(G_{n,d})
=
-
\lambda_{\max}(T_{(n-1,1)})?
\]

### Question 2

Which partition minimizes

\[
\lambda_{\min}(T_\lambda)?
\]

How does that partition depend on \(n\) and \(d\)?

### Question 3

Can the \(d=2\) Fibonacci tridiagonal standard block be diagonalized or asymptotically analyzed?

### Question 4

Can the proposed exact \(d=n-1\) spectrum be proved from permutation-module structure?

### Question 5

Do the resulting Hoffman bounds materially improve known elementary code/anticode bounds for Chebyshev permutation codes?

The first four are the immediate computational/structural targets. Do not spend time on literature comparison until the block implementation is reliable.
