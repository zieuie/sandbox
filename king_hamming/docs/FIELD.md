# Shared field partition

`kh_field` constructs one immutable SUD partition for a prime power `q = p^r`.
Every worker shares the same table. It does not retain a full coefficient table
or a logarithm table alongside the partition.

```sh
make -C king_hamming/solver
king_hamming/solver/kh_field 5 3 --threads 2 -o /tmp/field_5_3.bin
```

With no arguments, the program prints help and an example. Standard output gives
JSON metadata, including the primitive polynomial in increasing degree order.
The optional binary output is a native-endian array of unsigned 32-bit labels;
retain the metadata with it. This is an intermediate table, not a portable
matching certificate. Existing output files are refused.

## Fixed generator and residues

Label zero represents zero. Labels 1 through `q-1` represent
`1, X, X^2, ..., X^(q-2)`. Thus label 2 always represents `X`; in packed base-p
coefficient notation, the value of `X` is `p`.

The suffix contains the lowest `floor(r/2)` coefficients, packed in base p.
The prefix is the coefficient sum modulo p over the remaining, leading half.
There are `B = p^ceil(r/2)` cells, each containing
`F = p^floor(r/2)` labels. Cell members are sorted by label, with zero first
in its cell. These conventions agree with the preserved proof of concept.

## Primitive polynomial test

For each monic candidate, the library checks that `X^(q-1) = 1` and that
`X^((q-1)/l) != 1` for every distinct prime divisor `l` of `q-1`.
These checks give X exact order `q-1` in the polynomial quotient.

The quotient has q elements. Its `q-1` distinct powers of X are units, so every
nonzero element is a unit. Consequently it is a field, and the candidate is
irreducible as well as primitive. A different generator is never substituted.
Automatic generation enumerates packed lower coefficients from `--start`,
keeping the leading coefficient equal to one.

## Memory and parallel construction

The retained table uses `4q` bytes. Construction additionally uses
`4B * threads` bytes of counters, an allowance of 8 MiB per worker stack, and
64 MiB of fixed reserve. Admission checks this total against `--max-bytes`,
which defaults to 2 GiB; the command also sets an address-space limit.

Pinned workers first count their disjoint exponent ranges. Prefix sums assign
disjoint output segments in label order. A second pass writes those segments.
The passes synchronize before counters are repurposed, and no worker owns a
second complete field table.

`tests/check_field.py` compares every cell with the independent POC field for
2^3, 2^5, 3^3, 5^3 and 7^5, checks byte-identical results across thread counts,
and checks memory admission and output overwrite protection.

The production matching solver and its distributed ownership protocol remain
separate work. This builder establishes the shared representation they will use.
