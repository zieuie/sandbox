
make -C hamming2 odd_pattern

./odd_peek --pattern 2^5
./odd_pattern --pattern pattern_2_5.json -o M_33_32_192.pa.txt --verify

./odd_c --pattern pattern_2_5.json -o out.pa.txt

Markdown tables for a field:

```sh
python3 field_tables.py 2^5 1 0 0 1 0 1 -o gf_2_5_tables.md
python3 field_tables.py 3^3 1 0 2 1 -o gf_3_3_tables.md
```

This includes field labels, Sudborough sets, and the chosen partition-and-extension `P`/`Q` blocks.
Use `--no-pe` to print only the field/Sudborough tables.
