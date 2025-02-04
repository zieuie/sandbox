Sudborough provided a sketch of an iterative improvement algorithm
for making a (n, d)-PA where there are (n choose d) rows, each with
the d highest symbols in a different set of positions.

hill.py   - Sudborough's original algorithm
            Find best transposition, halt if none

hill2.py  - (useless now) using a different scoring mechanism
            for picking a best transposition

hill3.py  - (useless now) disturbing instead of halting
            instead of halting, pick a row and permute it

hill4.py  - pick the best permutation of high/low instead of transposing

hill5.py  - pick a random permutation instead of the best. This is faster!

hill6.py  - don't re-evaluate the whole PA's separation every iteration



Best strategy:
  Start with hill6. When it stagnates, switch to hill4.
  When hill4 stagnates, switch to hill9.

Questions to figure out:
  1. Why does hill6 get stuck? If you run hill4 then hill6, it'll even raise the disagreements to the stuck point.
  2. Why does hill4 get slower? It seems like it shouldn't?


Maybe hill6's disturbance strategy is weak