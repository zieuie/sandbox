# DP tiles bigger than any GPU: managed memory does not work (2026-10-13)

**Problem.** The last tiles of 167³ and 173³ (side 2048) need 6.05–7.33 GB of GPU memory (the
`gpu_fits` footprint: halo + half the values + p³·24 + 32 MiB). The largest GPU in the fleet,
merlin's RTX 3060, has 5.95 GB free, so they run on two CPU cores each: 40–55 h a tile, no
checkpoints, in dependency waves (173³: 4 + 3 + 2 + 1 tiles, 167³: 1 + 1). One of them died after
42 h (`[Errno 3] No such process`, cause not found) and restarted from zero.

**Idea tried.** Allocate the kernel's two big arrays with `cuMemAllocManaged` when they do not fit
(only then, only when asked), so the driver pages them between host and device. The change was a
dozen lines in `gpu_dp_solver/src/main.c` plus an optional binding in `cuda/kh_cuda.c`.

**Result.** Output is byte-identical (the 48-case suite passes with `KH_GPU_DP_FORCE_MANAGED=1`),
and a tile only 1.3× the free memory runs at full speed (8.4 s against 8.0 s) — but only because
its per-row working set (the p² rows behind the cell, 171 MB for 61⁵) still fit. With free memory
below that working set (4× oversubscribed, same tile) the run had not finished after nine minutes
(8 s fitting): paging thrashes. The big tiles are the worst case: every row of a tile reads cells
spread over the whole halo, so the working set of every row is the whole 6–7 GB array, past the
card. Not deployed; the code was reverted.

**What would work (not built).** Restructure the kernel around the static halo: for each slab of
halo rows that fits on the card, compute every tile row's partial maximum over the transitions that
read that slab (the halo is fixed input), keep the partial best value and id per cell (tile cells ×
12 bytes), merge across slabs with the existing tie-break by id, then run the in-tile rows. That is
a rewrite of `dp_row`'s scan with a merge, to be checked against `kh_dp_tile` byte for byte.

**Cheaper.** (1) Give the CPU tail tiles more cores (`max_cpus` on the root, now 2): waves still
sequential, each 2–3× shorter. (2) `campaigns/next_field.py` now prices the tail: fields whose
largest tile no GPU holds add one CPU wave per anti-diagonal of such tiles (179³: 26 h → 216 h).

The experiment script is `docs/oversubscription_experiment.py` (needs a free GPU; it takes the
device lock and a ballast process holding most of the card).
