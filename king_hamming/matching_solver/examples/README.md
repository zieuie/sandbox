# Verified parallel matching example

`13_5.khdp` is a saved DP split from the household campaign. `13_5.khmatch`
contains one complete matching for that split, found with four pinned matching
workers and the primitive polynomial `X^5 + 4X + 2`. Its KHM1 payload covers
371,293 left requests in 371,376 bytes, rather than storing the full grid.

From the repository root, independently check the certificate with:

```sh
python3 king_hamming/matching_solver/verify_match.py king_hamming/matching_solver/examples/13_5.khmatch --dp king_hamming/matching_solver/examples/13_5.khdp
```

Add `--hydrate /tmp/13_5_matching.tsv` to print individual assignments.
Verification checks the matching and the polynomial; it does not independently
recompute DP optimality. This is a local example, not a cluster-replicated result.
