# Unrestricted weighted Hoffman and Lovász theta

This phase uses the project-local virtual environment at `spectral/.venv`. To
recreate it on Ubuntu from the repository root:

```sh
python3 -m venv spectral/.venv
spectral/.venv/bin/python -m pip install -r spectral/4/requirements.txt
```

From the repository root, run the tests:

```sh
spectral/.venv/bin/python -m unittest spectral.4.test_theta -v
```

Run the full requested experiment:

```sh
spectral/.venv/bin/python -m spectral.4.run_theta
```

For a quicker validation that omits the larger `P(7,4)` theta SDP:

```sh
spectral/.venv/bin/python -m spectral.4.run_theta --skip-p74
```

Results are written under `spectral/4/results/`. SCS is the default because it
solves the larger `P(7,4)` model reliably in this environment. Clarabel solves
the small validation model but currently fails on the larger cone problem.

For a ChatGPT handoff, include `RESULTS_GUIDE.md`,
`LOVASZ_THETA_NEXT_PHASE.md`, and all files under `results/`.
