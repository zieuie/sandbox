# Spectral Experiments for Chebyshev-Distance Graphs on \(S_n\)

## Objective

Experiment computationally with the spectrum of the graph

\[
G_{n,d}=(S_n,E),
\]

where the vertices are permutations \(\sigma\in S_n\), and distinct vertices \(\sigma,\tau\) are adjacent iff their Chebyshev distance is at least \(d\):

\[
\delta_\infty(\sigma,\tau)=\max_{1\le i\le n}|\sigma(i)-\tau(i)|\ge d.
\]

The main goals are:

1. Verify the Cayley-graph formulation.
2. Compute spectra for small \(n,d\) directly.
3. Compute the same spectra via irreducible representations of \(S_n\).
4. Identify which irreducible representation supplies the least adjacency eigenvalue.
5. Test Hoffman-type clique/code bounds derived from that eigenvalue.
6. Look for patterns or closed forms, especially for the standard representation \((n-1,1)\).

A clique is exactly a permutation code with minimum Chebyshev distance at least \(d\).

---

## 1. Definitions

Represent a permutation \(\sigma\in S_n\) as the tuple

```text
(sigma(1), sigma(2), ..., sigma(n))
```

using values \(1,\dots,n\).

Throughout the mathematics, composition means

\[
(p\circ q)(i)=p(q(i)).
\]

The implementation uses the same convention with zero-based tuple entries. Thus
`compose(p, q)[i] == p[q[i]]`.

Define

\[
\delta_\infty(\sigma,\tau)=\max_i|\sigma(i)-\tau(i)|.
\]

Let

\[
r=d-1.
\]

Define the radius-\(r\) Chebyshev ball around the identity:

\[
B_r=\{\pi\in S_n:\max_i|\pi(i)-i|\le r\}.
\]

Define the complementary connection set

\[
C_d=S_n\setminus B_r
=\{\pi\in S_n:\max_i|\pi(i)-i|\ge d\}.
\]

The identity belongs to \(B_r\), not \(C_d\), when \(d\ge1\).

---

## 2. Cayley-graph formulation

The metric is right-invariant:

\[
\delta_\infty(\sigma,\tau)=\delta_\infty(\sigma\gamma,\tau\gamma).
\]

Equivalently, with a consistent permutation-composition convention,

\[
\delta_\infty(\sigma,\tau)=\delta_\infty(\tau\sigma^{-1},e).
\]

Therefore \(G_{n,d}\) is a Cayley graph of \(S_n\). One convenient convention is

\[
\sigma\sim\tau\iff \tau\sigma^{-1}\in C_d.
\]

The graph is undirected because

\[
C_d=C_d^{-1}.
\]

### Sanity check

For randomly chosen \(\sigma,\tau\), verify computationally that

```python
chebyshev(sigma, tau) == displacement(compose(tau, inverse(sigma)))
```

under the chosen composition convention.

---

## 3. Degree and the band-matrix permanent

Every vertex has degree

\[
k=|C_d|=n!-|B_r|.
\]

Let

\[
b_{n,r}=|B_r|.
\]

Then

\[
k=n!-b_{n,r}.
\]

Define the \(n\times n\) band matrix

\[
M_r=(m_{ij}),\qquad
m_{ij}=\begin{cases}
1,&|i-j|\le r,\\
0,&\text{otherwise}.
\end{cases}
\]

A permutation belongs to \(B_r\) iff every selected matrix entry \(m_{i,\pi(i)}\) is 1. Hence

\[
\boxed{b_{n,r}=\operatorname{per}(M_r)}.
\]

For small \(n\), enumerate \(S_n\) and count \(B_r\), then compare with a permanent computation.

---

## 4. Full adjacency matrix for small \(n\)

For small \(n\), explicitly construct

\[
A_{\sigma,\tau}=\begin{cases}
1,&\sigma\ne\tau\text{ and }\delta_\infty(\sigma,\tau)\ge d,\\
0,&\text{otherwise}.
\end{cases}
\]

The matrix is \(n!\times n!\), so this becomes expensive quickly.

Suggested range:

- \(n\le 6\): complete dense spectra should be routine.
- \(n=7\): use selected cases, sparse extremal eigensolvers, or Fourier blocks; a
  dense matrix has \(5040^2\) entries and is not a routine regression test.
- \(n=8\): do not build a dense matrix. Prefer representation blocks or a
  matrix-free Cayley operator.
- Larger \(n\): use representation-theoretic blocks instead of constructing \(A\).

Depending on \(d\), either the graph or its complement may be much sparser. Store
or apply the smaller connection set whenever only extremal eigenvalues are needed.

For each \((n,d)\), record:

- \(n!\)
- \(b_{n,r}\)
- degree \(k\)
- distinct adjacency eigenvalues
- multiplicities
- least adjacency eigenvalue
- second-largest absolute eigenvalue
- Laplacian spectral gap

### Sanity checks

Because there are no loops,

\[
\sum_i\lambda_i=0.
\]

Since the graph is \(k\)-regular on \(n!\) vertices,

\[
\sum_i\lambda_i^2=n!\,k.
\]

The largest eigenvalue must be

\[
k=n!-b_{n,r}.
\]

For connected cases, its multiplicity should be 1.

---

## 5. Extreme cases

### \(d=1\)

Every pair of distinct permutations has distance at least 1, so

\[
G_{n,1}=K_{n!}.
\]

Spectrum:

\[
n!-1
\]

once, and

\[
-1
\]

with multiplicity \(n!-1\).

### \(d=n\)

The maximum possible displacement is \(n-1\), so no edges occur. Thus \(G_{n,n}\) is edgeless and all eigenvalues are 0.

---

## 6. Group-algebra / Fourier decomposition

The regular representation decomposes as

\[
\mathbb C[S_n]\cong\bigoplus_{\lambda\vdash n}V_\lambda^{\oplus f^\lambda},
\]

where \(\lambda\vdash n\) ranges over partitions of \(n\), \(V_\lambda\) is the corresponding irreducible representation, and

\[
f^\lambda=\dim V_\lambda.
\]

For each partition define

\[
T_\lambda=\sum_{\pi\in B_r}\rho^\lambda(\pi).
\]

For the trivial representation \((n)\),

\[
T_{(n)}=b_{n,r}.
\]

The adjacency operator is the complete-group sum minus the ball sum. Therefore:

- trivial block:

\[
\lambda_{\mathrm{triv}}=n!-b_{n,r};
\]

- every nontrivial block:

\[
\operatorname{Spec}(A)|_\lambda=-\operatorname{Spec}(T_\lambda).
\]

If \(T_\lambda\) has an eigenvalue \(\mu\) of multiplicity \(m\), then the full adjacency matrix has eigenvalue

\[
-\mu
\]

with multiplicity

\[
m f^\lambda.
\]

This is the main representation-theoretic computation to implement.

There are two distinct extremal questions. On the nontrivial blocks,

\[
\lambda_{\min}(A)
=-\max_{\lambda\ne(n)}\lambda_{\max}(T_\lambda),
\]

whereas the Hoffman bound for the complement uses

\[
\min_{\lambda\ne(n)}\lambda_{\min}(T_\lambda).
\]

These extrema need not be supplied by the same partition and must be recorded
separately.

---

## 7. Important warning: generally not a normal Cayley graph

The set \(B_r\) is generally not a union of conjugacy classes.

For example,

\[
(1\,2)\qquad\text{and}\qquad(1\,n)
\]

are conjugate transpositions but have displacements

\[
1\qquad\text{and}\qquad n-1.
\]

Therefore \(T_\lambda\) need not be scalar.

Do **not** assume that character sums give every eigenvalue. Characters give block traces, but generally not full block spectra.

---

## 8. Sign representation

For the sign representation \((1^n)\),

\[
\rho^{(1^n)}(\pi)=\operatorname{sgn}(\pi).
\]

Therefore

\[
T_{(1^n)}=\sum_{\pi\in B_r}\operatorname{sgn}(\pi).
\]

By the determinant expansion of \(M_r\),

\[
T_{(1^n)}=\det(M_r).
\]

Hence the adjacency graph always has eigenvalue

\[
\boxed{-\det(M_r)}.
\]

### Computational check

For each \((n,r)\):

1. Construct \(M_r\).
2. Compute \(\det(M_r)\).
3. Confirm that \(-\det(M_r)\) occurs in the full adjacency spectrum.

---

## 9. Standard representation \((n-1,1)\)

Define the \(n\times n\) matrix

\[
Q_{ij}=\#\{\pi\in B_r:\pi(i)=j\}.
\]

Equivalently,

\[
Q_{ij}=m_{ij}\operatorname{per}(M_r^{(i,j)}),
\]

where \(M_r^{(i,j)}\) is obtained by deleting row \(i\) and column \(j\).

Every row sum and column sum is

\[
b_{n,r}.
\]

Thus \(\mathbf1\) is an eigenvector with eigenvalue \(b_{n,r}\).

The natural permutation representation decomposes as

\[
\mathbf1\oplus V_{(n-1,1)}.
\]

Therefore if

\[
\operatorname{Spec}(Q)=\{b_{n,r},\nu_2,\ldots,\nu_n\},
\]

then

\[
-\nu_2,\ldots,-\nu_n
\]

are adjacency eigenvalues. More precisely, if \(\nu\) has multiplicity \(m\) on
the standard subspace, then \(-\nu\) contributes multiplicity \(m(n-1)\) to the
regular representation. Other irreducible blocks may contribute the same
numerical eigenvalue, so its total full-spectrum multiplicity can be larger.

### Experiment

For each \((n,d)\):

1. Enumerate \(B_r\).
2. Build \(Q\) by counting.
3. Diagonalize \(Q\).
4. Remove the trivial eigenvalue \(b_{n,r}\).
5. Check that all negated remaining eigenvalues occur in the full adjacency spectrum.
6. Check whether one is the least adjacency eigenvalue.

This can be done for larger \(n\) than explicit diagonalization of the
\(n!\times n!\) adjacency matrix, but enumeration still costs
\(\Omega(|B_r|)\). For genuinely large \(n\), use a band-frontier/transfer-matrix
dynamic program to compute \(b_{n,r}\) and the constrained counts \(Q_{ij}\).

---

## 10. Symmetry of \(Q\)

Because \(B_r=B_r^{-1}\), expect

\[
Q_{ij}=Q_{ji},
\]

so \(Q\) should be symmetric.

There is also reversal symmetry under

\[
i\mapsto n+1-i,
\]

so expect

\[
Q_{ij}=Q_{n+1-i,n+1-j}.
\]

Check both identities computationally.

This may allow a decomposition into symmetric and antisymmetric subspaces under reversal.

---

## 11. Immanants and traces

For any irreducible character \(\chi^\lambda\),

\[
\operatorname{tr}(T_\lambda)=\sum_{\pi\in B_r}\chi^\lambda(\pi).
\]

This is the \(\chi^\lambda\)-immanant of \(M_r\):

\[
\operatorname{Imm}_{\chi^\lambda}(M_r)
=\sum_{\pi\in S_n}\chi^\lambda(\pi)\prod_i m_{i,\pi(i)}.
\]

Special cases:

- trivial character \(\to\operatorname{per}(M_r)\),
- sign character \(\to\det(M_r)\).

Use this as a block-trace sanity check when implementing irreducible representations.

---

## 12. Spectral norm bound

Because each \(\rho^\lambda(\pi)\) can be chosen unitary,

\[
\|T_\lambda\|\le |B_r|=b_{n,r}.
\]

Hence every nontrivial adjacency eigenvalue \(\theta\) satisfies

\[
|\theta|\le b_{n,r}.
\]

Check this numerically.

The normalized nontrivial eigenvalues satisfy

\[
\frac{|\theta|}{k}\le\frac{b_{n,r}}{n!-b_{n,r}}.
\]

---

## 13. Laplacian

The graph is \(k\)-regular, so

\[
L=kI-A.
\]

If \(\theta\) is an adjacency eigenvalue, then \(k-\theta\) is a Laplacian eigenvalue.

The norm bound gives nonzero Laplacian eigenvalues in

\[
[k-b_{n,r},\,k+b_{n,r}].
\]

Since \(k=n!-b_{n,r}\),

\[
\lambda_2(L)\ge n!-2b_{n,r}
\]

whenever that lower bound is positive.

Record the actual \(\lambda_2(L)\) and compare with this crude estimate.

---

## 14. Connectivity

For

\[
1\le d\le n-1,
\]

the graph is connected.

A proof under the fixed composition convention is:

- Put \(c=(1\,n)\). Then \(c\in C_d\), since its displacement is \(n-1\).
- For every adjacent transposition \(s_i=(i\,i+1)\), the product \(cs_i\)
  still has displacement \(n-1\): if \(i<n-1\), it maps \(n\) to \(1\),
  while if \(i=n-1\), it maps \(1\) to \(n\). Hence \(cs_i\in C_d\).
- Consequently \(s_i=c^{-1}(cs_i)\) belongs to the subgroup generated by
  \(C_d\). The adjacent transpositions generate \(S_n\), proving connectivity.

Computationally verify connectedness for all small \((n,d)\).

---

## 15. Coding-theory interpretation

A clique in \(G_{n,d}\) is exactly a subset

\[
\mathcal C\subseteq S_n
\]

such that every distinct pair satisfies

\[
\delta_\infty(\sigma,\tau)\ge d.
\]

Thus

\[
\omega(G_{n,d})
\]

is the maximum size of a Chebyshev permutation code with minimum distance \(d\).

An independent set is a set of pairwise distance less than \(d\).

The eventual goal is to determine whether spectral methods give useful upper bounds on \(\omega(G_{n,d})\).

---

## 16. Hoffman bound

For a \(k\)-regular graph on \(N\) vertices with least adjacency eigenvalue \(\tau<0\),

\[
\alpha(G)\le N\frac{-\tau}{k-\tau}.
\]

Apply this to the complement of \(G_{n,d}\).

The complement has degree

\[
k_{\mathrm{comp}}=b_{n,r}-1.
\]

If \(\mu\) is a nontrivial eigenvalue of the ball operator

\[
T=\sum_{\pi\in B_r}R_\pi,
\]

then the corresponding complement-adjacency eigenvalue is

\[
\mu-1.
\]

Let

\[
\mu_{\min}=\min_{\lambda\ne(n)}\lambda_{\min}(T_\lambda).
\]

Then the least complement eigenvalue is

\[
\tau=\mu_{\min}-1.
\]

Hoffman yields

\[
\boxed{
\omega(G_{n,d})\le
n!\,\frac{1-\mu_{\min}}{b_{n,r}-\mu_{\min}}
}
\]

provided \(\mu_{\min}<1\).

### Main experimental question

Which partition \(\lambda\vdash n\) minimizes

\[
\lambda_{\min}(T_\lambda)?
\]

Does it tend to be:

- \((n-1,1)\)?
- \((n-2,2)\)?
- \((n-2,1,1)\)?
- the sign representation?
- something depending strongly on \(d\)?

Record this systematically.

---

## 17. Brute-force clique numbers for comparison

For small \(n\), compute the exact clique number \(\omega(G_{n,d})\) using a maximum-clique solver.

Possible approaches:

- NetworkX for very small instances,
- a custom branch-and-bound maximum-clique implementation,
- MILP,
- an optimized external clique solver if already available.

Compare:

1. exact \(\omega(G_{n,d})\),
2. Hoffman bound using the exact least eigenvalue,
3. Hoffman bound using only the standard-representation eigenvalues,
4. elementary code/anticode bounds already available in the project.

The practical question is whether the spectral bound is competitive.

---

## 18. Suggested implementation phases

### Phase A: permutation utilities

Implement:

```python
all_permutations(n)
compose(p, q)
inverse(p)
chebyshev_distance(p, q)
displacement(p)
ball(n, r)
```

Use one explicit convention for composition and document it. Add unit tests.

### Phase B: explicit graph spectra

For small \(n\):

```python
adjacency_matrix(n, d)
spectrum_direct(n, d)
```

Use NumPy for dense matrices and SciPy sparse routines as sizes grow.

Return eigenvalues grouped by approximate equality. Because the adjacency matrix has integer entries, eigenvalues may be algebraic rather than integral, so use a numerical tolerance.

### Phase C: \(M_r\), permanent, determinant, \(Q\)

Implement:

```python
band_matrix(n, r)
permanent(M)
Q_matrix(n, r)
```

Initially compute permanents using Ryser's formula.

Checks:

```text
permanent(M_r) == len(B_r)
row_sums(Q) == b_{n,r}
column_sums(Q) == b_{n,r}
Q == Q.T
```

Compare the standard-representation spectrum from \(Q\) to the direct graph spectrum.

### Phase D: irreducible representations of \(S_n\)

Implement or use a library for irreducible representations indexed by partitions.

Good options to investigate:

- SageMath symmetric-group representations,
- GAP via SageMath,
- a direct Young orthogonal/seminormal implementation.

For experimentation, SageMath is probably the easiest route.

Desired API:

```python
partitions_of_n(n)
irrep_dimension(partition)
irrep_matrix(partition, permutation)
T_block(n, r, partition)
block_spectrum(n, r, partition)
```

Verify

\[
\sum_{\lambda\vdash n}(f^\lambda)^2=n!.
\]

Then verify that the union of all block spectra with the correct multiplicities reproduces the direct spectrum.

---

## 19. Young seminormal representation option

If implementing irreps directly, use standard Young tableaux.

For each partition \(\lambda\), basis vectors correspond to standard Young tableaux of shape \(\lambda\).

It is enough to implement adjacent transpositions

\[
s_i=(i\,i+1),
\]

because they generate \(S_n\).

In Young's orthogonal/seminormal form, \(s_i\) acts either:

- as \(+1\) if \(i,i+1\) lie in the same row,
- as \(-1\) if they lie in the same column,
- or on a two-dimensional span involving the tableau obtained by swapping \(i,i+1\).

Then decompose an arbitrary permutation into adjacent transpositions and multiply the corresponding matrices. Cache aggressively.

This is more work than using SageMath, but gives full control.

---

## 20. Avoid summing over all of \(S_n\) unnecessarily

The relevant operator is

\[
T_\lambda=\sum_{\pi\in B_r}\rho^\lambda(\pi).
\]

When \(B_r\) is much smaller than \(S_n\), enumerate only \(B_r\).

Generate \(B_r\) directly using backtracking constrained by

\[
|\pi(i)-i|\le r.
\]

Do not generate all \(n!\) permutations and filter once \(n\) becomes moderately large.

---

## 21. Direct generation of band-limited permutations

A simple starting point:

```python
def generate_ball(n, r):
    used = [False] * n
    p = [None] * n

    def rec(i):
        if i == n:
            yield tuple(p)
            return

        lo = max(0, i - r)
        hi = min(n - 1, i + r)

        for value in range(lo, hi + 1):
            if not used[value]:
                used[value] = True
                p[i] = value
                yield from rec(i + 1)
                used[value] = False

    yield from rec(0)
```

Potential optimization: choose the next row by minimum remaining values rather than simply increasing \(i\).

---

## 22. Data to collect

Create a CSV or JSON record for each \((n,d)\):

```text
n
d
r
factorial_n
ball_size
degree
num_partitions
least_adjacency_eigenvalue
partition_giving_least_adjacency_eigenvalue
largest_ball_operator_eigenvalue
partition_giving_largest_ball_eigenvalue
least_ball_operator_eigenvalue
partition_giving_least_ball_eigenvalue
standard_rep_min_eigenvalue
sign_eigenvalue
laplacian_gap
hoffman_clique_bound
exact_clique_number
```

For each partition, additionally save:

```text
partition
dimension
block_eigenvalues
block_trace
block_min_eigenvalue
block_max_eigenvalue
```

---

## 23. First experiment grid

Start with:

```text
n = 3..6
d = 1..n
```

For these values:

1. Build the full graph.
2. Compute its full spectrum.
3. Compute \(B_r\), \(M_r\), determinant, permanent, and \(Q\).
4. Verify all easy spectral predictions.
5. If SageMath/GAP is available, compute all Fourier blocks.
6. Compare exact clique numbers where feasible.

Use \(n=7\) only for selected sparse/matrix-free or block calculations. Then
push representation-theoretic calculations to

```text
n = 8, 9, 10, ...
```

without constructing the full graph.

---

## 24. Conjecture-hunting questions

Produce tables addressing:

### A. Extremal representation

Which \(\lambda\) gives the minimum eigenvalue of \(T_\lambda\)? Does one family dominate asymptotically?

### B. Standard representation

Does \((n-1,1)\) often give the extremal eigenvalue? Can its eigenvalues be characterized from the structured matrix \(Q\)?

### C. Reversal symmetry

Can \(Q\) be explicitly diagonalized using its centrosymmetric structure?

### D. Determinant pattern

What is

\[
\det(M_r)
\]

as a function of \(n,r\)? Does it satisfy a simple recurrence?

### E. Ball-size asymptotics

How large is

\[
b_{n,r}=\operatorname{per}(M_r)
\]

relative to \(n!\) in regimes such as:

```text
r fixed
r = alpha*n
d fixed
d = alpha*n
```

### F. Hoffman quality

When does the spectral clique bound improve meaningfully on elementary code/anticode bounds?

### G. Integral spectrum

For which small \((n,d)\), if any, is the graph integral?

### H. Extra multiplicity

Are there automorphisms beyond the right-regular \(S_n\) action that force additional eigenvalue multiplicities?

---

## 25. Possible extra graph automorphisms

Besides right multiplication, investigate the reversal permutation

\[
w_0(i)=n+1-i.
\]

Test whether maps such as

\[
\sigma\mapsto w_0\sigma w_0,
\qquad
\sigma\mapsto\sigma^{-1},
\]

or combinations of them preserve the metric.

Do not assume they are automorphisms; verify explicitly.

If the full automorphism group is larger than the regular \(S_n\) action, exploit it.

---

## 26. Numerical precision

Since \(B_r=B_r^{-1}\), the blocks \(T_\lambda\) are Hermitian when unitary realizations of the irreps are used.

Prefer Hermitian eigensolvers:

```python
numpy.linalg.eigvalsh
scipy.linalg.eigh
```

rather than generic eigensolvers.

Numerical eigenvalues should be real up to floating-point error.

For exact small examples, consider:

- SymPy,
- SageMath exact algebraic numbers,
- characteristic polynomials over \(\mathbb Z\).

---

## 27. Performance notes

The full graph becomes enormous:

```text
n=8:   40320 vertices
n=9:  362880 vertices
n=10: 3628800 vertices
```

Never build the full adjacency matrix for large \(n\).

Use:

- caching of representation matrices,
- direct generation of \(B_r\),
- sparse/structured methods where possible,
- multiprocessing over partitions,
- exact arithmetic only for small cases.

---

## 28. Minimal initial deliverable

Build a script or notebook that, for all \(3\le n\le6\) and \(1\le d\le n\):

1. enumerates \(B_{d-1}\),
2. computes \(b_{n,d-1}\),
3. constructs \(M_{d-1}\),
4. verifies
   \[
   b_{n,d-1}=\operatorname{per}(M_{d-1}),
   \]
5. constructs \(G_{n,d}\),
6. computes its full adjacency spectrum,
7. verifies the degree eigenvalue,
8. verifies the sign eigenvalue
   \[
   -\det(M_{d-1}),
   \]
9. constructs \(Q\),
10. verifies that the nontrivial eigenvalues of \(Q\), negated, occur in the adjacency spectrum with the expected multiplicities,
11. computes the exact least eigenvalue,
12. computes the Hoffman clique bound,
13. optionally computes exact clique number.

Output a human-readable table and machine-readable JSON.

---

## 29. Second deliverable

Using SageMath or GAP, implement the full irreducible block decomposition

\[
T_\lambda=\sum_{\pi\in B_{d-1}}\rho^\lambda(\pi).
\]

Confirm that the representation-theoretic spectrum exactly reproduces the directly computed spectrum for small \(n\).

Then report, for larger \(n\), which partition gives the extremal eigenvalue relevant to Hoffman.

---

## 30. Mathematical identities to test

The following should hold:

\[
|B_r|=\operatorname{per}(M_r),
\]

\[
k=n!-|B_r|,
\]

\[
\lambda_{\mathrm{trivial}}=k,
\]

\[
\lambda_{\mathrm{sign}}=-\det(M_r),
\]

\[
\operatorname{tr}(T_\lambda)=\operatorname{Imm}_{\chi^\lambda}(M_r),
\]

\[
\|T_\lambda\|\le|B_r|,
\]

\[
\sum_{\lambda\vdash n}(f^\lambda)^2=n!.
\]

For the full adjacency spectrum:

\[
\sum_i\lambda_i=0,
\]

\[
\sum_i\lambda_i^2=n!k.
\]

---

## 31. Main research target

The central quantity is

\[
\boxed{
\mu_{\min}(n,d)=
\min_{\lambda\ne(n)}
\lambda_{\min}\left(
\sum_{\pi\in B_{d-1}}\rho^\lambda(\pi)
\right)
}
\]

because it controls the Hoffman bound for Chebyshev permutation codes.

The most useful discoveries would be:

1. an explicit formula for \(\mu_{\min}(n,d)\),
2. a proof that a particular family of representations always attains it,
3. a manageable bound on \(\mu_{\min}(n,d)\),
4. a tractable exact description of the standard-representation matrix \(Q\),
5. a recurrence or asymptotic formula for relevant extremal eigenvalues,
6. evidence that spectral bounds improve existing code-size bounds.

---

## 32. Suggested repository layout

```text
spectral-chebyshev-sn/
├── README.md
├── requirements.txt
├── src/
│   ├── permutations.py
│   ├── chebyshev.py
│   ├── ball.py
│   ├── graph.py
│   ├── permanents.py
│   ├── standard_rep.py
│   ├── irreps.py
│   ├── spectrum.py
│   └── hoffman.py
├── experiments/
│   ├── small_spectra.py
│   ├── standard_rep_scan.py
│   ├── irrep_scan.sage
│   └── clique_comparison.py
├── tests/
│   ├── test_permutations.py
│   ├── test_ball.py
│   ├── test_spectrum.py
│   └── test_standard_rep.py
└── results/
    ├── small_spectra.csv
    ├── block_spectra.json
    └── plots/
```

---

## 33. Coding style

Prefer transparent mathematical code over premature optimization.

Every module should state:

- the permutation convention,
- whether indices are zero-based or one-based,
- whether matrices represent left or right actions.

Write assertions for the mathematical identities above.

When a pattern appears stable, print enough raw data to support conjecture formation rather than hiding everything behind plots.

---

# First task for Codex

Implement **Phase A through Phase C** first. Do not begin with general irreducible representations.

The initial target is to produce reliable small-\(n\) data and verify

\[
\boxed{
\begin{aligned}
|B_r|&=\operatorname{per}(M_r),\\
k&=n!-|B_r|,\\
\lambda_{\rm sign}&=-\det(M_r),\\
\operatorname{Spec}(A)&\supseteq -\left(\operatorname{Spec}(Q)\setminus\{|B_r|\}\right).
\end{aligned}
}
\]

Once those checks pass for all \(n\le6\), run selected \(n=7\) extremal checks
and proceed to the full \(S_n\) Fourier decomposition.
