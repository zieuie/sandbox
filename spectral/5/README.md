# Schrijver-strengthened theta

This experiment adds entrywise nonnegativity of the reconstructed invariant
primal matrix to the Fourier-domain theta SDP.

It uses the existing project-local virtual environment. From the repository
root:

```sh
spectral/.venv/bin/python -m unittest spectral.5.test_theta_prime -v
spectral/.venv/bin/python -m spectral.5.run_theta_prime
```

For a quick `P(5,3)` validation without the larger target:

```sh
spectral/.venv/bin/python -m spectral.5.run_theta_prime --skip-p74
```

Results go to `spectral/5/results/`. SCS is the default solver. Values close to
integer thresholds remain numerical evidence until certified exactly.

For a ChatGPT handoff, include `RESULTS_GUIDE.md`,
`THETA_PRIME_NEXT_PHASE.md`, and all three generated result files.
