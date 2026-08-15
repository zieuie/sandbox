# Phase D: irreducible spectral blocks

This experiment implements Young's real orthogonal representations directly;
SageMath is not required. It computes every block
\(T_\lambda=\sum_{\pi\in B_{d-1}}\rho^\lambda(\pi)\) and records the partitions
controlling the two distinct extrema.

From the repository root, run the validation suite:

```sh
python3 -m unittest spectral.second.test_phase_d -v
```

Reproduce and verify all Phase A--C cases for `n=3,4,5`:

```sh
python3 -m spectral.second.run_phase_d --min-n 3 --max-n 5
```

Run a selected larger case before a broad scan:

```sh
python3 -m spectral.second.run_phase_d --min-n 6 --max-n 6 --min-d 2 --max-d 3
```

Outputs go to `spectral/second/results/phase_d_blocks.json` and
`phase_d_summary.csv`. The JSON has every block spectrum; the CSV has one
extremal summary row per `(n,d)`.

The implementation also includes the Fibonacci tridiagonal formula for `d=2`,
the period-six determinant recurrence, and the proposed `d=n-1` spectrum.

To hand the generated results to ChatGPT or another analyst, include
`RESULTS_GUIDE.md` and `PHASE_D_GUIDE.md` with the JSON/CSV files. The results
guide defines the schema and includes a suggested analysis prompt.
