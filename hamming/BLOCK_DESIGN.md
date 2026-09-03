# Block Design Notes

This note is about the part of the construction that matters most for the matching step.

## The Main Distinction

There are two different combinatorial objects in the construction:

- the symbol-side blocks $Q_i$ used later by Partition and Extension,
- the position-side request blocks that drive the matching.

Those are related, but they are not the same thing.

In the current implementation, the matching graph is built from left nodes of the form
$$
(k,g,h),
$$
where:

- $k$ is the active affine slope index,
- $g$ is a residue class,
- $h$ is a suffix class.

The candidate set for such a node is
$$
N(k,g,h) = a_k^{-1} S_{g,h}.
$$

So the matching step is really choosing one representative from many shifted copies of the same Sudborough cells.

That means the most relevant design freedom for overlap is not “which final $Q_i$ sets do I print?” but rather:

> which residue-suffix cells do I ask each slope to realize on the matching side?

## What the Current Code Does

For a block of width $a$, the current code chooses the residues
$$
\{0,1,\dots,a-1\}
$$
and then takes all suffixes for each chosen residue.

So if a block has width $a$, its left-side request family is
$$
\{(k,g,h) : g \in \{0,\dots,a-1\},\ 0 \le h < F\}.
$$

This is simple, but it is only one possible design.

## Why Block Design Matters

The overlap graph for matching has:

- one node for each request block $(k,g,h)$,
- an edge when two candidate sets intersect.

That edge structure depends on:

1. the chosen slopes $a_k$,
2. the chosen residue sets for each block,
3. the fixed suffix expansion over all $h$.

The suffix part is forced once the block width is fixed. The place where you still have freedom is the residue selection.

So if you want the overlap graph to become easier to decompose, the natural design question is:

> can we choose the residue sets for each block so that the resulting shifted Sudborough cells intersect in a cleaner way?

## Three Natural Design Philosophies

The experiment script `experiment_block_designs.py` compares three heuristic choices.

### 1. Interval Design

Use consecutive residues:
$$
\{0,1,\dots,a-1\}.
$$

This is the current design. It is natural, easy to reason about, and matches the original overlap formula.

### 2. Spread Design

Use an arithmetic progression modulo $p$:
$$
\{r,\ r+s,\ r+2s,\dots\} \pmod p.
$$

This is trying to do for the matching side what weaving does for the symbol side: reduce collisions by spreading residues around the cycle instead of taking one clump.

### 3. Orbit Design

Use residues chosen from a multiplicative orbit in $\mathbf{F}_p^\times$, with a cyclic shift depending on the block.

This is a crude way of asking whether multiplicative symmetry, rather than additive interval structure, gives a cleaner overlap graph.

It is only a heuristic, but it is a useful probe.

## What These Experiments Are Really Testing

The experiments are not proving a new theorem. They are trying to answer:

- do some residue layouts create fewer overlap edges?
- do some layouts shrink the largest conflict component?
- does a more “symmetric” design make the graph easier to quotient by slope ratio?

If the answer is yes, then that is strong evidence that the matching problem can be reorganized around block design rather than treated as a generic graph problem.

## How to Read the Results

The most important outputs are:

- `edge_count`: how entangled the candidate blocks are overall,
- `component_sizes`: whether the graph naturally decomposes,
- `intersection_hist`: whether overlaps are mostly tiny or often large,
- `degree_hist`: whether most blocks see only a small local conflict neighborhood.

If one block design gives:

- fewer edges,
- smaller largest connected component,
- and smaller degrees,

then it is probably a better starting point for parallel or distributed matching.

## The Big Takeaway

For this construction, “block design” really means:

> choose the residue patterns on the matching side so that the inverse-slope images of the Sudborough cells collide as little, and as locally, as possible.

That is the right place to look if the goal is to make the matching step structurally easier before distributing it.
