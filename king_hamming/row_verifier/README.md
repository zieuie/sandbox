# Independent rendered-row verifier

`kh_verify_rows` is a deliberately direct C verifier for a completed KHM1
matching and its KHD1 dependency.  It does not call the DP or matching engines.
It independently:

1. checks both checksums and parses both portable formats;
2. constructs the quotient field and the SUD cells from the recorded primitive
   polynomial;
3. reconstructs the P and Q sets from the matching choices and DP split;
4. renders every affine row, applies partition-and-extend at the first covered
   position, and adds the freebie parallel class; and
5. compares every pair of rendered rows and requires Hamming distance at least
   `q`.

The implementation favors explicit arrays and loops over cleverness.  It is a
sanity checker, not a large-instance solver: quadratic pair comparison is
intentionally bounded by command-line limits.

```sh
make -C row_verifier
row_verifier/kh_verify_rows examples/5_3.khdp result.khmatch
row_verifier/kh_verify_rows --threads 4 --max-rows 100000 \
  examples/5_3.khdp result.khmatch
make -C row_verifier check
```

On failure it prints the first useful witness (bad request, uncovered affine
row, or too-close row pair) and exits nonzero.  `--threads 1` is the simplest
oracle path; additional threads only partition the outer pairwise loop.
