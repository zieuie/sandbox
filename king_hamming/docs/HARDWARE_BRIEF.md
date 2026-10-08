# King Hamming: project and hardware brief

Figures from the live cluster on 2026-10-04.

## What the project does

A research computation in combinatorics. For a prime power q = pʳ (r odd), it builds
large *permutation arrays* (sets of permutations of q + 1 symbols that pairwise differ in
at least q positions) and proves lower bounds on their maximum size, M(q+1, q). Each
prime power ("field") goes through two stages:

1. **DP (dynamic programming).** Chooses the construction's shape. The DP table is split
   into tiles of up to 4096 × 4096 that run as independent jobs, in wavefront order.
   Integer work; it runs on GPUs whenever one is free (C and CUDA, byte-identical
   to the CPU version). For the current fields, a GPU is 30–300× faster than 2 CPU
   threads, so in practice **DP throughput is GPU throughput**: the GPUs are 93–98%
   busy, and only 15 of about 7,900 recent tiles ran on a CPU. A tile uses about
   0.3–0.4 GiB of host RAM and about 313 MiB of VRAM, and passes only about 25 KB of
   edge data to the next tiles over the network.
2. **Matching.** Finds a perfect bipartite matching with about q requests (one per field
   element), then writes and independently verifies a certificate of about
   q · log₂(p^⌊r/2⌋) / 8 bytes. Memory-bound and needs one large machine: an NVIDIA GPU
   does the search in blocks, while host RAM holds the field rows (4 bytes × p^(r−1) × a,
   where a is usually 3–5) and an occupancy bitmap of q/8 bytes. Fast NVMe holds several
   copies of the certificate.

The cluster software is our own: a SQLite leader with leases and replication (Python), C
and CUDA kernels, Ubuntu 24.04, NVIDIA driver 580 (no CUDA toolkit needed on workers).
New machines join by running an agent. Any x86-64 Linux box with an NVIDIA GPU (or no
GPU) works, and mixed hardware is fine.

## Current fleet (10 machines, all used laptops and mini-PCs)

pellinore (Dell XPS 15 9570, GTX 1050 Ti Max-Q, 2 × 8 GB DDR4 SO-DIMMs) was retired on
2026-10-04: no faster than a P600 mini-PC, with the most energy per tile.

| Machines | CPU | Cores / threads | RAM | GPU (VRAM) | Disk |
|---|---|---|---|---|---|
| 8 × mini desktop (dp-101 to dp-108) | i7-7700T | 4 / 8 | 16 GB | Quadro P600 (2 GB) | 256 GB NVMe |
| merlin (dp-151), MSI GE76 laptop: also leader and dashboard | i7-11800H | 8 / 16 | 40 GB | RTX 3060 Laptop (6 GB) | 512 GB NVMe |
| gawain (dp-156), Lenovo laptop | i7-9850H | 6 / 12 | 8 GB | Quadro T1000 (4 GB) | 1 TB NVMe |

- **Totals:** about 46 physical cores, about 170 GB RAM, about 26 GB of GPU memory.
- **Network:** every machine is on one unmanaged 1 Gb/s switch (about 105 MB/s
  measured), with Wi-Fi alongside for control traffic and internet. Control traffic is
  tiny: about 75 KB/s for the whole cluster.

## Where the work stands

- **Done:** 82 fields are fully DP'd, matched and certified. The largest is 7¹³
  (q = 9.7 × 10¹⁰, matched 2026-10-07 by the wide matcher, a 206 GB certificate on merlin's second
  drive); then 5¹⁵
  (q = 3.05 × 10¹⁰, matched 2026-10-07 by the wide matcher, a 64.8 GB certificate); then 31⁷
  (q = 2.75 × 10¹⁰, matched 2026-10-06, a 51.6 GB certificate); before it 29⁷
  (q = 1.72 × 10¹⁰, matched 2026-10-05): a 32.3 GB certificate, matched on merlin's RTX 3060
  in 109 blocks using about 18 GB of host RAM. Before it, 13⁹ (q = 1.06 × 10¹⁰, a 19.9 GB
  certificate) needed 16.5 GiB to verify.
- **DP:** wrapped up on 2026-10-06 with 31⁷; 5¹⁵ was added for the wide matcher's rehearsal.
- **Was blocked: 7¹³** (q = 9.7 × 10¹⁰), matched on 2026-10-07 by the wide matcher with merlin's second drive (below is the analysis that led there; see [GPU_WIDE_MATCHING_PLAN.md](GPU_WIDE_MATCHING_PLAN.md)):
  - **RAM:** about 166 GB of field rows plus a 12 GB bitmap, so about 180–200 GB on one
    machine.
  - **Disk:** about 190 GB of scratch choices, a 206 GB certificate, and copies, so about
    0.6–1 TB of free fast disk.
  - **Software:** the current code is capped at q < 2³⁶ (6.9 × 10¹⁰). Raising it is a
    software change, not a hardware one.
- **Cancelled earlier:** 107³, 109³ and 113³ (reason not on record).

## What limits us, in order

1. **One big-memory machine for matching and verifying the largest fields.** RAM is the
   hard limit: 7¹³ needs about 200 GB in one box. An NVIDIA GPU with more VRAM means
   fewer, bigger blocks (6 GB works today). NVMe matters for multi-hundred-GB
   certificates.
2. **GPU throughput for DP tiles.** Per-tile GPU kernel times on 29⁷ tiles: P600
   47 s, 1050 Ti Max-Q 48 s, T1000 18.5 s, RTX 3060 Laptop 5.5 s. The same tile takes
   about 1,600 s on 2 threads of an i7-7700T. VRAM barely matters (313 MiB per tile).
3. **SSD wear.** Each tile writes about 350 MB of scratch to disk and deletes it:
   about 0.5 TB/day on a mini-PC and 4.6 TB/day on merlin (about 1% of its drive's
   rated life per day). This can be fixed in software by keeping tile scratch in RAM,
   but it rules out low-endurance drives until then.
4. **Fewer points of failure.** merlin is the leader, the dashboard and the only
   big-RAM matcher. The cluster has been hurt by a Wi-Fi roaming outage (5 workers
   offline for 4.5 h) and by agents not starting at boot.

## Questions for the hardware search

- **Big box:** the cheapest way to get a single x86-64 Linux machine with **256 GB RAM**,
  about **2 TB NVMe**, and an NVIDIA GPU with 8–24 GB, for example a used workstation or
  server (Xeon or EPYC with DDR4 RDIMMs) plus a used RTX 3060 12 GB / 3090 / A4000.
  Noise, power and size matter in a home.
- **Or:** is it better to add GPU throughput for DP (for example a desktop with one or
  two used RTX 3060 12 GB / 3070 / 4060-class cards, or a GPU upgrade for the
  mini-PCs if their chassis allows one), keep matching on merlin, and rework the
  software to split 7¹³ across machines or stream its rows from NVMe?
- **Upgrades that help today:** RAM matters only for the matching machine, and VRAM
  hardly at all; DP wants faster GPUs. Is 64 GB possible in merlin (MSI GE76)?
- **Rough power draw** and running cost of whatever is suggested; the cluster runs 24/7.

## Follow-up answers (measured on the live cluster, 2026-10-04)

Mostly from the last 6 hours of tiles. Correction to an earlier version of this
brief: **DP is GPU-bound, not CPU-bound.**

### 1. CPU vs GPU split

In the last 6 hours about 7,900 tiles finished for the current fields (29⁷ and 31⁷).
Only 15 of them ran on a CPU; the rest ran on GPUs. Kernel time per 4096² tile:

| Engine | 29⁷ | 31⁷ |
|---|---|---|
| i7-7700T, 2 threads (CPU) | ~1,580 s | ~2,440 s |
| Quadro P600 | 47 s | 58 s |
| GTX 1050 Ti Max-Q | 48 s | 61 s |
| Quadro T1000 | 18.5 s | 25 s |
| RTX 3060 Laptop | 5.5 s | 6.5 s |

- **Throughput per machine:** each mini-PC does about 68 tiles/h, gawain about
  150/h, and merlin about 550/h. merlin's one laptop 3060 does about 40% of all DP
  work, as much as all eight mini-PCs together.
- **It depends on the field.** 7¹³ has few transitions per cell, so its tiles were
  short: 2–22 s of kernel time on CPU or GPU, mostly overhead, and CPUs ran most of
  them. The big remaining fields have thousands of transitions per cell, and there
  the GPU dominates.

### 2. What limits the GPU kernel

- **Not profiled.** There's no Nsight on the fleet, so it isn't known for certain
  whether the kernel is limited by integer compute or by memory bandwidth.
- **The design:** each cell scans its transition list, reading neighbouring
  predecessor values that should mostly come from cache.
- **Don't trust spec sheets for this:** the 1050 Ti Max-Q has twice the P600's cores
  and more bandwidth, yet runs at the same speed. That points to the laptop
  throttling on power or heat, not to the architecture. Buy on measured speed; a
  full-power desktop card should beat a laptop card of the same name.
- **VRAM is irrelevant:** a tile uses about 313 MiB, so 2 GB is plenty.
- **One tile at a time per GPU.** A host-wide lock enforces it.
- **Running more tiles at once wouldn't help on most machines:** the GPUs are already
  93–98% busy.
- **merlin is the exception at 64% busy:** its tiles finish so fast that per-tile CPU
  and disk overhead leaves the GPU idle. That can be fixed in software.

### 3. GPU architectures

- **What's embedded:** the binaries contain precompiled code for sm_61, sm_75, sm_86
  and sm_89, plus compute_61 PTX. They're compiled at build time with NVRTC and load
  through `libcuda`, so workers need no CUDA toolkit.
- **Cards that run without a rebuild:** an RTX 3060 desktop (sm_86) or any 40-series
  card (sm_89).
- **Newer cards:** the driver can compile the PTX for anything newer, such as sm_120
  on the 50-series. That should work but is untested.
- **Driver constraint:** the Pascal cards (P600, 1050 Ti) keep the fleet on the NVIDIA
  580 driver branch. 580 also supports current cards.

### 4. Matching access pattern

It depends on the stage:

- **Block matching (the GPU part):** sequential and local. Each block owns a
  contiguous range of cells and uploads only those rows, as one contiguous slice.
  Exchange rounds would touch scattered rows, but 13⁹ needed none. Memory-mapping
  the rows from NVMe would work for this stage.
- **Building the rows:** random. Each consecutive power of X lands in a
  pseudo-random cell, so this writes scattered across the whole table. It would need
  restructuring into passes, one cell range per pass. Each pass enumerates the whole
  field: about 1–3.5 h per pass for 7¹³, scaling from 13⁹.
- **Verifying:** random as written. For each coset it reads one 4-byte label from
  every used row, so 7¹³ from NVMe would mean about 10¹¹ random reads, which is
  infeasible. A multi-pass verifier (one cell range at a time, streaming the 206 GB
  certificate each pass) would make it sequential. The 12 GB bitmap of used positions
  still needs random access, so it stays in RAM.
- **Bottom line:** 7¹³ on a 64 GB machine with fast NVMe looks feasible with real
  software work, mainly a multi-pass row build and verifier. 256 GB of RAM avoids
  most of that work. Either way, the 2³⁶ size cap needs lifting.

### 5. 13⁹ matching time split

- **The stages:** field rows 23 min (CPU), blocks 11 min (GPU: 169 blocks, all
  matched in the first round, no exchange), result write 20 min (disk), publish
  3.5 min, verification about 2 h (single-threaded, CPU and RAM).
- **GPU share:** about 11 of roughly 3 hours.
- **Would more VRAM help?** Not much. It means fewer, larger blocks, worth a few
  minutes at most.
- **What would help:** more cores and faster CPUs for the row build, faster NVMe for
  the write, and a multithreaded verifier (a software change).
- **Scaled to 7¹³ (estimate):** roughly 9× everything, so verification alone would be
  about 18 h single-threaded.

### 6. Disk per worker

- **Capacity:** small. Each worker holds 6–51 GB: a 1.6–47 GB blob store plus about
  4–5 GB of work files. 128–256 GB drives are plenty for capacity.
- **Speed:** DP doesn't need fast disks.
- **Endurance is the problem.** Every tile writes about 350 MB of scratch to the SSD
  and deletes it: a 200 MB `halo.bin` plus its outputs. That's about 20 GB/h
  (0.5 TB/day) on a mini-PC and about 190 GB/h (4.6 TB/day) on merlin.
- **Drive wear:** merlin's 512 GB PM981 shows 8% wear after 39 TB written, so it's
  gaining about 1% of rated life per day. The mini-PCs' drives show 4–38% wear.
- **The fix:** software, keeping tile scratch in RAM (tmpfs). Until then, avoid
  low-endurance budget drives (QLC, low TBW).

### 7. Network

- **Per tile:** about 25 KB of edge data in, and about 57 KB of packet plus about
  25 KB of bands out. Results are stored 3 times, so about 250 KB of network traffic
  per tile. That's about 0.1 MB/s for the whole cluster at the current 1,300
  tiles/h; measured wired traffic is about 75 KB/s.
- **Peaks:** large matching certificates, for example 20 GB for 13⁹ in 3 copies, take
  a few minutes per copy at about 105 MB/s.
- **The 1 Gb/s switch:** nowhere near a bottleneck.
- **Wi-Fi alone:** would work for DP. Big certificates would take 15–20 min per copy
  at Wi-Fi's ~20 MB/s.
