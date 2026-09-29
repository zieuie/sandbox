# Result utilities

Run commands from the repository root. Each utility prints help when invoked
without arguments.

## Print the collected results as Markdown

Refresh the local collection, then print or save the table:

```sh
python3 king_hamming/dp_solver/launch_dp.py collect
python3 king_hamming/scripts/print_results.py \
    king_hamming/cluster/deployments/dp-campaign/results

python3 king_hamming/scripts/print_results.py \
    king_hamming/cluster/deployments/dp-campaign/results > results.md
```

You can supply `index.json` instead of the directory. Without an index, the
utility reads all `.khdp` files in the directory. Each retained run gets its own
row, sorted by field size. The table includes p, r, q, theta, predicted rows, and
a link to the compact artifact. It checks artifact checksums and split feasibility.
Predicted rows are not a completed matching result.

Links are relative to the current directory. When saving elsewhere, specify that
report's directory:

```sh
python3 king_hamming/scripts/print_results.py \
    king_hamming/cluster/deployments/dp-campaign/results \
    --link-base king_hamming > king_hamming/results.md
```

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
