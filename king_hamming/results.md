# Results

Lower bounds M(q + 1, q) ≥ N proved by the campaign, as of 2026-10-06. Each number N is
the exact number of permutations in the array (q + F²·θ, with F = p^⌊r/2⌋). A `^` means
the field's matching certificate was found and independently verified, so the bound is
proved; see [docs/MATCHING_CERTIFICATE.md](docs/MATCHING_CERTIFICATE.md) for why that is
enough.

Each number is the exact number of permutations (rows) in the array. `^` means a completed full matching; `*` means a certified Hall obstruction for a tested polynomial (not necessarily every polynomial). `(running)` means DP or matching is in progress; `—` means no completed DP value.

| Prime \ Exponent | 3 | 5 | 7 | 9 | 11 | 13 | 15 | 17 | 19 | 21 | 23 | 25 | 27 | 29 | 31 | 33 | 35 | 37 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 28^ | 192^ | 1,472^ | 11,264^ | 89,088^ | 704,512^ | 5,619,712^ | 44,826,624^ | 358,350,848^ | 2,864,709,632^ | 22,913,482,752^ | 183,274,307,584^ | 1,466,127,351,808^ | 11,728,481,943,552^ | 93,826,781,806,592^ | 750,605,664,518,144 | 6,004,828,136,275,968 | (running) |
| 3 | 144^ | 3,483^ | 90,396^ | 2,407,887^ | 64,717,704^ | 1,744,720,803^ | 47,083,546,836^ | 1,271,040,530,967^ | 34,316,157,233,664^ | — | (running) | — | — | — | — | — | — | — |
| 5 | 1,375^ | 159,375^ | 19,609,375^ | 2,443,359,375^ | 305,224,609,375^ | 38,148,193,359,375^ | 4,768,402,099,609,375^ | — | — | — | — | — | — | — | — | — | — | — |
| 7 | 6,076^ | 1,990,429^ | 678,717,081^ | 232,569,366,743^ | 79,760,841,208,636^ | 27,357,428,724,361,309^ | — | — | — | — | — | — | — | — | — | — | — | — |
| 11 | 47,190^ | 61,375,072^ | 81,534,323,464^ | 108,502,034,795,770^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 13 | 99,710^ | 215,407,062^ | 472,708,712,606^ | 1,038,436,628,063,094^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 17 | 338,130^ | 1,642,523,986^ | 8,064,313,527,762^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 19 | 555,940^ | 3,776,181,296^ | 25,887,466,479,060^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 23 | 1,296,050^ | 15,667,458,067^ | 190,564,823,479,032^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 29 | 3,741,609^ | 90,969,067,658^ | 2,218,287,697,118,362^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 31 | 5,053,899^ | 150,087,862,357^ | 4,470,713,705,180,443^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 37 | 11,288,774^ | 569,651,235,950^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 41 | 17,862,306^ | 1,228,544,806,926^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 43 | 22,143,624^ | 1,756,432,945,357^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 47 | 32,845,621^ | 3,401,742,737,444^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 53 | 55,899,100^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 59 | 90,659,164^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 61 | 105,776,867^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 67 | 161,478,308^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 71 | 212,599,134^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 73 | 240,662,969^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 79 | 342,225,235^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 83 | 427,503,784^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 89 | 588,102,566^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 97 | 859,201,653^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 101 | 1,041,542,502^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 103 | 1,125,933,170^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 107 | 1,336,613,505^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 109 | 1,466,091,638^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 113 | 1,709,437,106^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 127 | 2,874,865,218^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 131 | 3,356,485,668^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

Notes:
- **85 fields are proved.** Every one matched with the first polynomial the solver could
  finish (5¹⁵'s first two failed from a solver defect, below); no Hall obstruction has ever been
  found.
- **7¹³** was matched on 2026-10-07 by the wide GPU matcher on merlin (723 blocks, 7 passes,
  about 17.5 h; the 191.7 GiB certificate is on merlin's second drive) and verified twice, by the
  agent and by the feeder (`kh_verify_wide`); the largest field proved
  ([docs/GPU_WIDE_MATCHING_PLAN.md](docs/GPU_WIDE_MATCHING_PLAN.md)).
- **29⁷** was matched on 2026-10-05 (GPU blocks on merlin, 109 blocks) and verified.
- **31⁷** was matched on 2026-10-06 (GPU blocks on merlin, 181 blocks) and verified: the
  campaign's last DP field from the regular frontier.
- **5¹⁵** was matched on 2026-10-07 by the wide GPU matcher on merlin (236 blocks, 3 passes;
  its third polynomial, 3,4,3,0,…,1) and verified. The first two polynomials ended short from a
  block-layout defect, not the field: see
  [docs/WIDE_MATCHING_SPARSE_TAIL.md](docs/WIDE_MATCHING_SPARSE_TAIL.md).
- **2³¹ and 3¹⁹** were added on 2026-10-06 (Zooey) as the cheapest fields left, with DP on the
  P600s and gawain (about an hour each, 2048-cell tiles) and matching by GPU blocks on dp-102
  and dp-101 (about 20 minutes each); both verified.
- **41⁵, 43⁵, 47⁵, 127³ and 131³** (2026-10-08) and **2³³, 2³⁵, 2³⁷, 3²³** were started by the hourly
  yet calculated" rule (`campaigns/next_field.py`). 41⁵ and 43⁵ matched on a single GPU in
  minutes. 2³³ and 2³⁵ have DP values only (no `^`): no matcher takes p = 2 past F = 65,534 yet
  (the wide matcher refuses p = 2, since F is a power of two). They are the first fields with
  r above 31; the DP's old cap of 31 had no width reason and was lifted on 2026-10-08.
- **107³, 109³ and 113³** were cancelled before their DP finished in September; Zooey retried
  them from the dashboard on 2026-10-06, and all three were matched and verified by 2026-10-07 08:00.

Regenerate this table from the live leader database with
`python3 campaigns/result_table.py` (read-only; it re-checks every certificate's hashes and
header, about a minute).
