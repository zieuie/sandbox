# The matching certificate, for dummies

What a `.khmatch` file is, why checking it proves a permutation array exists, and what
the checker actually does. The maths is from the paper
([`prime_power_09_26.pdf`](prime_power_09_26.pdf), sections 2, 4 and 5); the checking
is in `matching_solver/src/verify_khm1.c` and `matching_solver/artifacts.py`.

## 1. The claim

A *permutation array* is a list of permutations (orderings) of the same symbols in
which every two of them disagree in at least `d` positions. For a prime power `q`,
each result in the campaign claims

> there is a permutation array on `q + 1` symbols, with every pair disagreeing in at
> least `q` positions, containing **N** permutations.

That is, `M(q+1, q) ≥ N`. For 13⁹ the claim is N = 1,038,436,628,063,094.

Nobody can check that by listing the permutations: there are about 10¹⁵ of them,
each 10¹⁰ symbols long. The certificate is a much smaller file that proves they exist
without listing them.

## 2. The recipe the claim relies on

The construction is a recipe with one step that can fail. Everything else is
guaranteed by the paper's theorems.

**Ingredients.** Build the finite field GF(q) from a *primitive polynomial*. Its
elements give `q(q−1)` permutations of `q` symbols (the maps `x ↦ ax + b`), and any
two of them disagree in at least `q − 1` places. They come in `q − 1` groups called
*cosets*, each a Latin square: inside a coset, two permutations never agree anywhere.

**The trick (partition and extension).** We want one more symbol (call it `★`) and one
more disagreement. For a coset `Cᵢ`, pick a set of *positions* `Pᵢ` and a set of
*symbols* `Qᵢ`. Every permutation in `Cᵢ` that has, at some position `x` in `Pᵢ`, a
symbol from `Qᵢ` is *extended*: put `★` at position `x` and move the old symbol to a new
last position. Permutations with no such position are dropped. One extra coset, the
*freebie*, simply gets `★` appended at the end.

**Why that works, if the sets don't overlap.** Take two extended permutations from
different cosets. Before extending they agreed in at most one place. After extending:

- their new last symbols came from `Qᵢ` and `Qⱼ`, so if those sets don't overlap, the
  last symbols differ;
- the `★`s sit at a position in `Pᵢ` and a position in `Pⱼ`, so if those sets don't
  overlap, the `★`s are in different places and can't create a new agreement.

So they still agree in at most one place out of `q + 1`, and disagree in at least `q`.
Two permutations from the same coset, or involving the freebie, work out the same way.
This is Theorem 1 of the paper (from reference [8]).

**How many survive.** The dynamic program (DP) decides each coset's *shape*. Coset `i`
gets a stripes and b special sets, and a run of `t` cosets shares the same shape. The
paper proves (Lemmas 6 and 7) that the number of permutations that survive depends only
on that shape, never on which exact field elements are used:

    N = q + F² · θ      where F = p^⌊r/2⌋ and θ = Σ t · ω(a, b, t)

The `q` is the freebie. The DP artifact stores the shape and θ, and `verify_dp.py`
checks the arithmetic separately (section 6).

**The step that can fail.** The symbol sets `Qᵢ` are easy: whole blocks of the field,
handed out in turn, so they never overlap. The position sets `Pᵢ` are the hard part.
Every coset needs `a` stripes, each stripe needs `F` positions with prescribed
properties, and no position may be used twice anywhere. In total that is a few billion
non-overlapping choices for 13⁹. The paper is honest about this (section 5.3.2):
"There may not be a matching in all cases, but a matching has been found in all cases
tried." There is no theorem that says it always works.

The certificate is the evidence that it worked for this particular field.

## 3. The search as a matching problem

Picking the positions is a *bipartite matching* problem, like seating guests:

- **Guests (requests).** One for each position the shape needs: coset `i`, stripe
  `g` (its *prefix residue*, 0 to a−1), and suffix `β`. There are `n = F · Σ a·t` of
  them; for 13⁹, n = q = 10,604,499,373.
- **Chairs.** The `q` field elements; each position of the permutations is one of them.
- **Who may sit where.** Request `(i, g, β)` accepts any of the `F` field elements in
  *cell* `(g, β)`: the elements whose last ⌊r/2⌋ coefficients spell `β` and whose
  leading coefficients add up to `g` mod p. Each of those elements is then rotated by
  the coset's *shift*, which slides every nonzero element `X^k` to `X^(k−i)` (zero
  stays put). Any of
  the `F` candidates gives a valid stripe; the only issue is collisions with other
  requests.

A full matching (every guest gets a chair, no chair shared) is exactly a valid,
non-overlapping choice of every `Pᵢ`. Finding one takes a real search (many rounds of
reseating guests along chains of swaps). Checking one takes no search at all: one
straight pass over the requests.

## 4. What the certificate contains

For each request, in a fixed order, it stores a single small number `k` between 0 and
F−1: "this request took the k-th element of its cell", counting the cell's elements in
increasing label order. That's all. It holds no permutations and no field elements:
everything else can be recomputed from the polynomial and the DP shape.

Layout of a KHM1 file:

| Part | Contents |
|---|---|
| `KHM1` | format tag |
| 32 bytes | SHA-256 of the DP artifact it belongs to (the shape) |
| varints | `p`, `r`, the polynomial's coefficients (lowest degree first), outcome (0 = full matching), `n`, matched count |
| packed choices | `n` numbers of ⌈log₂ F⌉ bits each, lowest bit first, zero padding |
| 32 bytes | SHA-256 of everything before it, to catch corruption |

Request order: cosets in DP order; within a coset, stripe `g = 0 … a−1`; within a
stripe, suffix `β = 0 … F−1`.

Size is about `n · ⌈log₂ F⌉ / 8` bytes. For 13⁹, F = 28,561 needs 15 bits, so the file
is 19,883,436,416 bytes (18.5 GiB). It's big, but writing each position out as a full
field label would take 34 bits each, and the permutations themselves would take
about 5 × 10²⁵ bytes.

## 5. A tiny example you can check by hand: 3³ = 27

The DP shape for `q = 27` (`examples/3_3.khdp`) is five cosets:

| Coset | a | b | ω | Requests (a·F) | Permutations kept (F²·ω) |
|---|---|---|---|---|---|
| 0 | 1 | 1 | 1 | 3 | 9 |
| 1, 2 | 1 | 3 | 3 | 3 each | 27 each |
| 3, 4 | 3 | 1 | 3 | 9 each | 27 each |

θ = 1 + 3 + 3 + 3 + 3 = 13, so N = 27 + 9 · 13 = **144**, the paper's M(28, 27) ≥ 144.
There are 27 requests, one for each of the 27 field elements, so every chair is taken.

The certificate (made with the polynomial X³ + 2X + 1, i.e. `1,2,0,1`) is 84 bytes; the
choices themselves are 7 bytes:

    choices: 0 0 0 2 0 0 1 0 2 2 0 2 2 2 1 0 0 1 2 0 0 2 1 2 0 1 2

Checking request 3 (coset 1, stripe 0, suffix 0) by hand:

1. Cell (0, 0) is `{0, 5, 18}`: the labels whose low coefficient is 0 and whose two
   leading coefficients sum to 0 mod 3. (Label 18 is X¹⁷ = 2X² + X, coefficients
   (2, 1, 0): suffix 0, prefix 2 + 1 ≡ 0.)
2. The choice is 2, so take the third element, label 18 = X¹⁷.
3. Shift for coset 1: X¹⁷ becomes X¹⁶, which is label 17.
4. Position 17 is marked used; no other request may land there.

Doing that for all 27 requests gives 27 different positions, so the certificate is
valid. `verify_match.py --hydrate rows.tsv` prints this whole table.

## 6. What the checker does, step by step

`verify_match.py` (with the native `kh_verify_khm1` doing the per-request loop for big
fields) runs these steps; each one rules out a specific way of cheating or failing:

1. **Checksum.** The trailing SHA-256 matches, so the file is not truncated or corrupted.
2. **Right shape.** The embedded DP hash equals the hash of the DP file given, and `p`,
   `r` and `n` agree with it, so the matching can't be presented against a different,
   easier shape.
3. **Real field.** The polynomial is monic and *primitive*: X has order exactly q − 1.
   That makes the labels a genuine copy of GF(q), so the paper's theorems apply.
   Rebuilding the field also checks that X cycles back to 1 after q − 1 steps.
4. **Cells as promised.** Every cell has exactly F elements (the paper's prefix/suffix
   partition).
5. **Every edge legal.** Each choice `k` is below F, so the request really takes an
   element of its own cell. The shift is recomputed, not read from the file.
6. **No position used twice.** A q-bit table marks each position; a repeat is
   rejected. This is the one property the whole proof needed.
7. **Everyone seated.** All n requests were decoded, and the padding is zero.

The checker shares no code with the solvers: it rebuilds the field with its own
arithmetic and doesn't trust the GPU, the cluster or the matcher. For 13⁹ it streams
the 18.5 GiB file once and needs about 16.5 GiB of memory (the cell rows plus the
q-bit table).

Separately, `dp_solver/verify_dp.py` checks the DP file: the shape fits the budget
(Σ a·t and Σ b·t at most p·F), and θ equals Σ t·ω(a, b, t), with ω recomputed
directly. It also re-runs the DP to confirm θ is optimal (feasible for small fields). That matters for "best
possible with this method", not for the lower bound.

## 7. What it proves and what it doesn't

**Proves:** for this prime power and polynomial, the paper's construction with this DP
shape has non-overlapping position sets, so the extended permutation array exists, has
minimum distance q on q + 1 symbols, and has N = q + F²θ members. That is,
M(q+1, q) ≥ N, and anyone can re-check it without rerunning the search.

**Does not prove:**

- That N is the true maximum. It's a lower bound; better constructions may exist.
- Anything about other polynomials: a failure with one polynomial is not a failure of
  the field.
- That the matching was found any particular way, or is unique. Any valid matching
  gives the same N.

**Even powers** (r even) never need a certificate: there every request has the same
number of options as every position has requests, and Hall's theorem guarantees a
matching (paper, Theorem 3). The campaign's fields are odd powers, where that argument
doesn't apply and the certificate is the proof.

**Failed searches** have a certificate too (outcome 1): a *Hall witness*, a set of
requests that together can reach fewer positions than there are requests. The same
checker recounts that neighbourhood. It proves only that *this* shape cannot be
matched with *this* polynomial.

**In practice, none has failed.** As of 2026-10-07 the campaign has matched 92 fields,
up to 7¹³, and every one succeeded with the first primitive polynomial the solver could finish
(5¹⁵'s first two ended short from a solver defect, not a Hall obstruction:
[WIDE_MATCHING_SPARSE_TAIL.md](WIDE_MATCHING_SPARSE_TAIL.md)). All 73
archived certificates are full matchings and none is a Hall witness (checked from each
file's outcome field and the leader's run records). That is empirical support for the
paper's remark that a matching has been found in every case tried, now at much larger
sizes, but it is not a proof that one always exists.

## 8. Try it

```sh
cd king_hamming
python3 matching_solver/match.py examples/3_3.khdp -o /tmp/m.khmatch --threads 1
python3 matching_solver/verify_match.py /tmp/m.khmatch --dp examples/3_3.khdp --hydrate /tmp/rows.tsv
python3 dp_solver/verify_dp.py examples/3_3.khdp
```

The second command prints `"verified": true`, and `/tmp/rows.tsv` lists
request → coset, stripe, suffix, position for all 27 requests.
