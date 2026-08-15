# Chebyshev spectral experiments

This directory contains the Phase A--C implementation described in
`spectral_chebyshev_sn_experiment.md`. It is self-contained and does not modify
or import the other research programs in the repository.

See `RESULTS_GUIDE.md` for a compact explanation that can be shared with the
generated JSON/CSV when asking another model to analyze the results.

Run the tests from the repository root:

```sh
python3 -m unittest spectral.test_chebyshev_spectra
```

Run a quick complete experiment:

```sh
python3 -m spectral.run_experiments --min-n 3 --max-n 5
```

Results are written under `spectral/results/`. Dense full spectra are capped at
`n=6`; larger Phase A--C counts can be requested with `--no-full-spectrum`.
