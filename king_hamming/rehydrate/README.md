# kh_rehydrate: certificate in, permutation array out

A single-file C program that reads a matching certificate (`.khmatch`) and the DP shape it belongs
to (`.khdp`), prints **every piece of metadata either file holds**, and optionally writes out the
**permutation array** the certificate proves exists, so you can check the results with your own code.

```sh
cc -O2 -std=c11 -o kh_rehydrate kh_rehydrate.c      # or: make        (no dependencies; SHA-256 is built in)

# Metadata only. Instant, even on the 192 GiB certificate for 7^13: it reads just the header and trailer.
./kh_rehydrate DP.khdp MATCH.khmatch
./kh_rehydrate --state ../cluster/deployments/continuous-campaign --field 7,13

# Write the array (text, one row per line):
./kh_rehydrate --state ../cluster/deployments/continuous-campaign --field 5,3 --output 5_3.txt
# Binary instead (1, 2 or 4 bytes per symbol, little endian, no header), or part of the array:
./kh_rehydrate --state ... --field 13,3 --format bin --output 13_3.bin
./kh_rehydrate --state ... --field 13,3 --classes 0:2 --output first_two_cosets.txt
```

`--state DIR --field P,R` finds the full-matching certificate for p^r in `DIR/matching-results` (or
`DIR/results`) and the DP file it belongs to, by comparing hashes. Or name the two files.

## What it prints

- **Certificate:** path, size, format tag, the embedded DP hash and whether it matches, `p`, `r`,
  the **primitive polynomial** (coefficients lowest degree first, the polynomial itself, and the
  reduction rule), the outcome, the request count, the matched count, bits per choice, header bytes,
  packed bytes, whether the size and padding are as the format requires, and the trailer hash
  (`--verify-checksum` also hashes the whole file against it; that reads every byte).
- **DP shape:** path, size and hashes, `p`, `r`, `q`, `F`, the budget `p*F`, `theta`, the runs
  `(a, b, t, repeat)` with each run's first coset, first request and rows per coset, the totals
  (cosets, requests, special sets), and the claim `N = q + F^2 * theta` permutations of `q+1` symbols
  with pairwise distance at least `q`.
- **Field:** `q - 1` factored, and the primitivity of the polynomial shown by `X^(q-1) = 1` and
  `X^((q-1)/s) != 1` for each prime `s` dividing `q - 1`. This needs no tables, so it is instant for any `q`.
- **The whole array:** rows, width, symbols and bytes it would need if written out.
- **After a render:** rows written, output size and SHA-256, and (for the whole array) the checks below.

`--explain` prints every convention the rendering uses, and `--requests PATH` writes one line per
certificate request (index, coset, stripe, suffix, choice, label, `X^k`, coefficients, cell, position).

## The output format

Rows are permutations of the symbols `0..q`. **Symbols `0..q-1` are the field's labels** (label 0 is the
zero element, label `k+1` is `X^k`); **symbol `q` is the extension symbol** (the paper's star). Every row has
`q + 1` entries, the last being the new final position. Rows come coset by coset (cosets `0..cosets-1`,
translates in increasing label order, dropped rows omitted), then the freebie class. The metadata report
(also saved as `OUTPUT.meta.txt`) spells out the construction.

Writes refuse to overwrite a file, and refuse more than `--max-bytes` (2 GiB by default).

## It cannot write the big ones

The array is `N` rows of `q + 1` symbols. 5^3 is 173 thousand symbols and 13^3 is 219 million (438 MB as
binary), but 7^5 is 33 billion, 13^9 is about 10^25 and 7^13 is about 2.7 x 10^27. Rendering is for fields with
`q` in the low thousands; `--classes A:B` renders a few cosets of larger ones (tables need `q < 2^32`).
For the large fields the metadata, the polynomial check and `--requests` are the useful part.

## What it checks while it writes (whole array)

- every row is a permutation of `0..q`;
- the matching never uses a position twice (and uses exactly `n` of them);
- the rows kept per coset equal `F^2 * omega` from the DP, per run;
- the total equals `N = q + F^2 * theta`;
- optionally (`--verify-checksum`) the certificate's own hash.

These are consistency checks. **They are not the distance check**: running the pairwise comparison is yours
to do (or `row_verifier/kh_verify_rows`, which does it for small fields).

## How independent is it?

It shares no code with the solvers, the cluster, or `row_verifier`. But I wrote its conventions (labels,
cells, the shift applied to the matching's positions, which special sets go to which coset, the first-covered-position
extension rule) by reading `row_verifier/verify_rows.c`, then checked them against the paper's construction
in `docs/prime_power_09_26.pdf`. So the two programs being byte-identical shows this one is a faithful
reimplementation; it does **not** show the conventions are right. For an independent check, read `--explain`
against the paper (sections 2, 4 and 5), then check the distances in your own code.

Tested against `kh_verify_rows --render` byte for byte on 2^3, 3^3, 2^5, 5^3, 2^7, 3^5, 7^3 and 2^9 (each
also verified there for minimum distance = q), and by `make check`. 3^7, 11^3 and 13^3 pass the internal
checks above, and 13^3 had 4.5 million row pairs compared independently (minimum distance exactly 2197 = q).

Exit status: 0 if everything checked is consistent, 1 if not, 2 for a usage error.
