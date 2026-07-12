# Odd-Power Hamming Construction

This folder contains a reconstruction of an old construction for building large permutation arrays under the Hamming metric when
$$
q = p^r
$$
is an odd power of a prime. The main output is a lower bound of the form
$$
M(q+1,q) \ge |A|,
$$
obtained by applying Partition and Extension to carefully chosen cosets of the affine general linear group $\mathrm{AGL}(1,q)$.

The code is useful, but the main thing worth remembering is the mathematical shape of the construction.

## Goal

Start with permutations of the $q$ field elements of $\mathbf{F}_q$, arranged as the $q(q-1)$ elements of
$$
\mathrm{AGL}(1,q)=\{x \mapsto ax+b : a \in \mathbf{F}_q^\times,\ b \in \mathbf{F}_q\}.
$$

Each fixed nonzero $a$ gives a coset of size $q$:
$$
\mathcal{C}_a=\{x \mapsto ax+b : b \in \mathbf{F}_q\}.
$$

The construction chooses several cosets $\mathcal{C}_{a_1},\dots,\mathcal{C}_{a_k}$, attaches to the $i$-th coset:

- a set $P_i$ of positions,
- a set $Q_i$ of symbols,

and then applies Partition and Extension to obtain permutations on $q+1$ symbols. A final untouched coset is used as a freebie block.

So the size of the resulting permutation array is
$$
q + \sum_{i=1}^k |\mathcal{C}_{a_i}^\star|,
$$
where $\mathcal{C}_{a_i}^\star \subseteq \mathcal{C}_{a_i}$ is the subset of permutations covered by $(P_i,Q_i)$.

The point of the construction is to make every chosen coset fully covered, so the target size becomes
$$
q(k+1).
$$

## Field Viewpoint

Write
$$
r=2L+1.
$$
Then every field element is represented in a polynomial basis as an $r$-digit vector over $\mathbf{F}_p$:
$$
c_0 + c_1\alpha + \cdots + c_{r-1}\alpha^{r-1}.
$$

Two pieces of data are extracted from each field element $x$:

- its residue
  $$
  \operatorname{pre}(x)=c_0+\cdots+c_{r-1}\pmod p,
  $$
- its suffix
  $$
  \operatorname{suf}(x)=(c_0,\dots,c_{L-1}).
  $$

Since the suffix has length $L$, there are
$$
F = p^L
$$
possible suffixes, and $p$ possible residues.

This partitions the $q=p^{2L+1}$ field elements into $pF$ classes
$$
S_{d,s}=\{x \in \mathbf{F}_q : \operatorname{pre}(x)=d,\ \operatorname{suf}(x)=s\}.
$$
Each such class has size $F$, because fixing one residue and $L$ suffix digits leaves exactly $L$ free digits.

That partition is the raw combinatorial material from which the $P_i$ and $Q_i$ are built.

## The Sudborough Sets

The construction begins with the family
$$
\{S_{d,s}\}_{d \in \mathbf{F}_p,\ s \in \mathbf{F}_p^L}.
$$

These sets have two attractive features:

- they are uniform, each of size $F$,
- multiplication by a nonzero field element permutes them in a structured way.

The code uses the affine cosets in the order $x \mapsto ax+b$ with $a=1,2,\dots$, and interprets multiplication by $a$ as the map connecting the symbol side $Q_i$ to the position side $P_i$.

At a high level:

- the $Q_i$ are unions of selected Sudborough sets,
- the $P_i$ are built so that multiplication by the corresponding slope $a_i$ sends every chosen position into the intended $Q_i$-side structure.

## Why Residues and Suffixes?

The residue controls one linear condition, while the suffix controls $L$ coordinate conditions. Together they cut the field into blocks of size $F$, which is exactly the scale needed when $r=2L+1$.

This gives a natural rectangular bookkeeping system:

- $pF$ residue-suffix blocks on the symbol side,
- $pF$ corresponding blocks on the position side,
- each successful match contributes $F$ actual field elements,
- each residue interaction is repeated across all suffix choices.

That is what makes the later dynamic-programming count come out in multiples of $F^2$.

## The Split Problem

The core optimization problem is: how should the Sudborough blocks be grouped into Partition-and-Extension blocks $(P_i,Q_i)$?

The code parametrizes a local pattern by a triple
$$
(a,b,t),
$$
where informally:

- $a$ is how many position-side residue classes are used,
- $b$ is how many symbol-side residue classes are used,
- $t$ is a spacing parameter saying that the relevant $Q$-residue classes are taken $t$ apart cyclically modulo $p$.

For such a pattern, the number of distinct residue differences hit is
$$
\operatorname{overlap}(p,a,b,t)
=
\left|\left\{gt-G \pmod p : 0 \le G < a,\ 0 \le g < b\right\}\right|.
$$

Every distinct residue difference contributes an entire $F \times F$ grid of suffix choices, so one block of type $(a,b,t)$ contributes
$$
t \cdot F^2 \cdot \operatorname{overlap}(p,a,b,t).
$$

The factor $t$ appears because the pattern yields $t$ actual Partition-and-Extension blocks.

## Why Coverage Looks Like This

The key question for one affine slope $a$ is:

> for how many positions $x$ does the image $ax$ land in a symbol block we want?

If we were working with arbitrary subsets of $\mathbf{F}_q$, this would be too messy to count cleanly. The residue-suffix partition is what makes it tractable.

Suppose a position block wants:

- $a$ residue classes on the position side,
- all $F$ suffix choices inside each of those residue classes.

Suppose the matching symbol block wants:

- $b$ residue classes on the symbol side,
- all $F$ suffix choices inside each of those residue classes.

Now forget suffixes for a moment and only track residues modulo $p$. Multiplication by a fixed nonzero slope permutes the field, so the interesting question becomes:

> which residue differences between the position side and symbol side can actually occur?

Once one such residue difference is allowed, the suffix bookkeeping is completely free on both sides, which gives an entire $F \times F$ family of actual field elements. That is why the final counts are always multiples of $F^2$.

So the high-level logic is:

1. count how many residue interactions are possible,
2. multiply by $F^2$ because each residue interaction lifts to all suffix pairs,
3. multiply by the number of woven copies.

That is exactly the origin of
$$
t \cdot F^2 \cdot \operatorname{overlap}(p,a,b,t).
$$

## Why the Overlap Formula Is the Right One

Take:

- position residues $G \in \{0,\dots,a-1\}$,
- symbol residues indexed by $g \in \{0,\dots,b-1\}$,
- and place the symbol residues $t$ apart, so their residue locations are $0,t,2t,\dots,(b-1)t \pmod p$.

For a position residue $G$ and a symbol residue $gt$, what matters is their relative displacement modulo $p$:
$$
gt - G \pmod p.
$$

Why relative displacement? Because the affine multiplication step does not care about the absolute names of the residue classes, only about how one residue class lines up against another after the slope action. Two different pairs $(G,g)$ that produce the same difference modulo $p$ are not giving genuinely new residue behavior; they are reusing the same alignment.

So the quantity we really care about is not the raw number $ab$ of pairs, but the number of distinct differences they generate:
$$
\left|\left\{gt-G \pmod p : 0 \le G < a,\ 0 \le g < b\right\}\right|.
$$

That is the overlap.

You can think of it as the size of the Minkowski-style difference set
$$
\{0,t,2t,\dots,(b-1)t\} - \{0,1,\dots,a-1\}
$$
inside the cyclic group $\mathbf{Z}/p\mathbf{Z}$.

Large overlap is good because each new residue difference gives another full $F^2$ block of usable field elements.

Small overlap means many choices on the position side and symbol side are collapsing onto the same residue behavior, so the construction is wasting potential coverage.

## Why Weave by $t$ at All?

The parameter $t$ is the way the construction spreads the symbol-side residue classes around the cycle modulo $p$ instead of taking them as a single consecutive chunk.

Without weaving, the symbol residues would just be
$$
0,1,2,\dots,b-1,
$$
and the difference set with the position residues could bunch up badly. In other words, many position/symbol pairs would produce the same residue difference, so the overlap would be smaller than it could be.

By taking the symbol residues $t$ apart:
$$
0,t,2t,\dots,(b-1)t \pmod p,
$$
the construction tries to spread those differences more evenly around the residue cycle.

The purpose of weaving is therefore:

- to avoid collisions among residue differences,
- to enlarge the overlap set,
- to turn the same budget $(a,b)$ into more actual covered field elements.

So $t$ is a spacing parameter whose job is to trade local regularity for global spread.

## Why the Same $t$ Creates $t$ Blocks

The subtle point is that a woven pattern with spacing $t$ does not describe just one block. It naturally breaks into $t$ translated copies of the same residue geometry.

Intuitively, if the symbol-side residues are taken modulo $p$ in steps of $t$, then there are $t$ interleaved threads running through the residue cycle. Each thread supports one actual Partition-and-Extension block.

That is why the contribution is not merely
$$
F^2 \cdot \operatorname{overlap}(p,a,b,t),
$$
but rather
$$
t \cdot F^2 \cdot \operatorname{overlap}(p,a,b,t).
$$

So:

- $\operatorname{overlap}(p,a,b,t)$ counts how much one woven thread can hit,
- the outer factor $t$ counts how many such threads the weaving creates.

## A Small Toy Picture

Suppose $p=7$, $a=2$, $b=2$.

If $t=1$, then the symbol residues are $\{0,1\}$ and the position residues are $\{0,1\}$. The difference set is
$$
\{0,1\} - \{0,1\} = \{-1,0,1\},
$$
so the overlap size is $3$.

You can see that directly in a table of differences $gt-G \pmod 7$:

| symbol residue $gt$ | position residue $G=0$ | position residue $G=1$ |
| --- | --- | --- |
| $0$ | $0$ | $-1 \equiv 6$ |
| $1$ | $1$ | $0$ |

So the distinct residues hit are $\{0,1,6\}$, which has size $3$.

If $t=3$, then the symbol residues are $\{0,3\}$. The difference set becomes
$$
\{0,3\} - \{0,1\} = \{-1,0,2,3\},
$$
so the overlap size is $4$.

Again, the table makes the extra spread visible:

| symbol residue $gt$ | position residue $G=0$ | position residue $G=1$ |
| --- | --- | --- |
| $0$ | $0$ | $-1 \equiv 6$ |
| $3$ | $3$ | $2$ |

Now the distinct residues are $\{0,2,3,6\}$, which has size $4$.

That is already better: with the same $a=2$ and $b=2$, the woven choice sees more distinct residue differences.

Then the full contribution is multiplied by:

- $F^2$, because every residue difference lifts to all suffix pairs,
- and by $t$, because the spacing-$t$ pattern yields $t$ interleaved blocks.

So weaving is not cosmetic. It is the mechanism that makes the residue geometry less redundant.

## The Dynamic Program

The dynamic program fills a table $V[p',q']$, where:

- $p'$ is the remaining amount of position-side budget,
- $q'$ is the remaining amount of symbol-side budget.

The full budget is
$$
pF \quad \text{on each side},
$$
because there are $p$ residue classes and $F$ suffix classes.

The recurrence is
$$
V[p',q']
=
\max_{a,b,t}
\left(
tF^2\operatorname{overlap}(p,a,b,t)
+ V[p'-ta,\ q'-tb]
\right),
$$
subject to the obvious feasibility constraints.

The output is:

- the optimal value $V[pF,pF]$,
- a list of triples $(a,b,t)$ describing the chosen split.

Conceptually, this split says how many Partition-and-Extension blocks to make, and how large each one should be on the $P$-side and the $Q$-side.

## Building the $Q_i$

Once the split is chosen, the $Q_i$ are formed explicitly as unions of Sudborough sets.

The algorithm walks through the residue-suffix blocks in a structured order and bundles them according to the $(a,b,t)$ pattern. The important thing mathematically is that each $Q_i$ is a disjoint union of whole Sudborough blocks, not arbitrary individual field elements.

This preserves the clean counting from the dynamic program.

## Building the $P_i$

The $P_i$ are not chosen greedily. They are obtained by a matching problem.

For each tentative $P_i$, the construction knows which residue-suffix blocks it wants after multiplying by the slope corresponding to that coset. So it creates a bipartite graph:

- left side: requests of the form "block $i$ needs a position from residue class $g$ and suffix class $h$",
- right side: actual field elements $x \in \mathbf{F}_q$,
- edge rule: $x$ is adjacent to the request if multiplying $x$ by the coset slope lands in the desired Sudborough block.

A maximum matching then assigns distinct field positions to all requests. The matched elements become the actual sets $P_i$.

This is the step that turns the counting argument into an explicit construction.

## Partition and Extension

Now fix a coset
$$
\mathcal{C}_{a_i} = \{x \mapsto a_i x + b : b \in \mathbf{F}_q\},
$$
and its associated pair $(P_i,Q_i)$.

A permutation $\pi\in \mathcal{C}_{a_i}$ is called covered if there exists a position $j \in P_i$ such that
$$
\pi(j)\in Q_i.
$$

For a covered permutation:

1. locate a covered position $j$,
2. replace $\pi(j)$ by the new symbol $q$,
3. append the displaced symbol $\pi(j)$ at the end.

This produces a permutation on $\{0,1,\dots,q\}$.

That is the standard one-symbol Partition-and-Extension move.

For the extra freebie coset, no position is modified; the new symbol $q$ is simply appended at the end of every row.

## Why the Distance Improves

Inside a single affine coset, two distinct rows differ in every position, so their Hamming distance is $q$. After extension, the rows coming from the same block still stay far apart.

Across different blocks, the point of the partitions $P_i$ and $Q_i$ is to control where the new symbol can appear. The Partition-and-Extension theorem then raises a distance-$(q-1)$ affine-family construction on $q$ symbols to a distance-$q$ construction on $q+1$ symbols.

So the whole argument is:

1. choose many affine cosets,
2. fully cover each chosen coset with a compatible pair $(P_i,Q_i)$,
3. apply Partition and Extension,
4. add one untouched freebie coset.

That yields the final lower bound for $M(q+1,q)$.

## The Naive Variant

There is also a simpler version that does not optimize the split. It takes a full collection of $F$ blocks directly from the residue-suffix partition and therefore gives a smaller but easier construction.

Mathematically, the naive version is the same story without the dynamic-programming optimization.

## Replication

The `--replicate` mode takes one successful Partition-and-Extension instance and cyclically shifts it to produce many related instances, called "pis" in the old notes.

If one construction uses $k$ active cosets plus one freebie coset, then the number of disjoint replicas is
$$
\left\lfloor \frac{q-1}{k+1} \right\rfloor.
$$

This is why the replicated output naturally reports a total coverage that is a multiple of the single-instance size.

## Worked Example: $q=3^3=27$

This is the smallest nontrivial case in the folder, and it is a good scale for remembering what each piece means.

Here:

- $p=3$,
- $r=3$,
- $L=1$,
- $F=p^L=3$.

So each field element has three base-$3$ coefficients
$$
x = c_0 + c_1\alpha + c_2\alpha^2,
$$
and we record:

- residue: $c_0+c_1+c_2 \pmod 3$,
- suffix: just the first coefficient $c_0$.

That gives $pF = 3 \cdot 3 = 9$ Sudborough sets, each of size $F=3$.

So the field is partitioned into a $3 \times 3$ grid:

- 3 residue classes,
- 3 suffix classes,
- 3 field elements per cell.

For this case, the optimized construction in the sample `M_28_27_144.xtar.json` produces 4 active blocks. The position-set sizes are
$$
3,\ 3,\ 3,\ 9.
$$

Those 4 blocks are attached to the first 4 affine cosets
$$
\mathcal{C}_1,\mathcal{C}_2,\mathcal{C}_3,\mathcal{C}_4,
$$
and all 4 are fully covered.

Each active coset contributes 27 extended rows, so the active part contributes
$$
4 \cdot 27 = 108.
$$

Then one extra freebie coset contributes another
$$
27
$$
rows.

So the final permutation array size is
$$
108 + 27 = 144,
$$
which is exactly the bound recorded in the file name.

That is the cleanest way to remember the bookkeeping:

- every active block corresponds to one affine slope,
- full coverage of that slope yields $q$ rows after extension,
- one extra untouched slope yields the freebie $q$ rows,
- total size is $q(k+1)$ when $k$ active cosets are fully covered.

So the `3^3` example is the memory hook:

- 9 Sudborough cells,
- grouped into 4 active Partition-and-Extension blocks,
- matched to 4 affine cosets,
- plus 1 freebie coset,
- giving $27 \cdot (4+1)=144$ rows.

## Files in This Folder

- [`odd.py`](./odd.py) builds the partitions $P_i,Q_i$ and writes the old-style xtar payload.
- [`xtar_to_pa.py`](./xtar_to_pa.py) reconstructs the affine group, applies Partition and Extension, and prints the explicit permutation array.
- [`verify.py`](./verify.py) is a simple Hamming-distance checker for the final array.

## A Good Mental Summary

If you only want the memory hook, it is this:

1. represent $\mathbf{F}_{p^{2L+1}}$ in a polynomial basis,
2. partition field elements by digit-sum residue and $L$-digit suffix,
3. use dynamic programming to decide how many residue classes each block should consume,
4. use matching to realize those blocks as actual position sets,
5. pair each block with an affine coset $x \mapsto ax+b$,
6. apply Partition and Extension,
7. add a freebie coset.

That is the whole construction in one line:  
structured field partition -> optimized block split -> matching realization -> affine cosets -> Partition and Extension.
