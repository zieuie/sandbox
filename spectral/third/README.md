# Weighted spectral bounds

This experiment optimizes weighted forbidden-distance operators using the
irreducible blocks from Phase D. No external SDP package is required: a
cutting-plane method uses SciPy/HiGHS and separates violated PSD constraints by
their least-eigenvalue eigenvectors.

From the repository root, run the tests:

```sh
python3 -m unittest spectral.third.test_weighted -v
```

Run the requested Stage 1 and Stage 2 experiments for `P(5,3)` and `P(7,4)`:

```sh
python3 -m spectral.third.run_weighted
```

Run only shell weights:

```sh
python3 -m spectral.third.run_weighted --families shell
```

Outputs go to `spectral/third/results/`, including the full JSON, a CSV summary,
a Markdown report, and the best `P(7,4)` weight assignment.

These are numerical SDP certificates. Any bound lying extremely close to an
integer threshold should be reconstructed and verified exactly before being
claimed as a theorem.

For a ChatGPT handoff, include `RESULTS_GUIDE.md` and
`WEIGHTED_SPECTRAL_NEXT_PHASE.md` with the four generated result files.
