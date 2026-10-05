# Result utilities

Run commands from the repository root. Each utility prints help when invoked
without arguments.

## Print the results table

The project's `results.md` (prime × exponent, with which bounds are proved) comes from the
live leader database:

```sh
python3 king_hamming/campaigns/result_table.py > king_hamming/results.md
```

It is read-only and re-checks every matching certificate's hashes and header (about a
minute).

## List DP artifacts

To list the DP results in one directory (p, r, q, θ, predicted rows and a link to each
compact artifact), use `print_results.py`. It checks checksums and split feasibility, not
matchings:

```sh
python3 king_hamming/scripts/print_results.py \
    king_hamming/cluster/deployments/continuous-campaign/results
```

You can supply `index.json` instead of the directory. Links are relative to the current
directory; pass `--link-base DIR` when saving the output elsewhere.

## Inspect individual artifacts

```sh
python3 king_hamming/scripts/print_dp.py PATH_TO_SPLIT.khdp
python3 king_hamming/scripts/print_field.py
python3 king_hamming/scripts/print_matching.py
```

These lightweight printers share the standalone artifact inspection code. The
production DP printer and independent verifier are also available:

```sh
python3 king_hamming/dp_solver/print_dp.py PATH_TO_SPLIT.khdp --verify
```
