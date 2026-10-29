# Results

Lower bounds M(q + 1, q) ≥ N proved by the campaign, as of 2026-10-29. Each number N is
the exact number of permutations in the array (q + F²·θ, with F = p^⌊r/2⌋). A `^` means
the field's matching certificate was found and independently verified, so the bound is
proved; see [docs/MATCHING_CERTIFICATE.md](docs/MATCHING_CERTIFICATE.md) for why that is
enough.

Each number is the exact number of permutations (rows) in the array. `^` means a completed full matching; `*` means a certified Hall obstruction for a tested polynomial (not necessarily every polynomial). `(running)` means DP or matching is in progress; `—` means no completed DP value.

| Prime \ Exponent | 3 | 5 | 7 | 9 | 11 | 13 | 15 | 17 | 19 | 21 | 23 | 25 | 27 | 29 | 31 | 33 | 35 | 37 | 39 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 28^ | 192^ | 1,472^ | 11,264^ | 89,088^ | 704,512^ | 5,619,712^ | 44,826,624^ | 358,350,848^ | 2,864,709,632^ | 22,913,482,752^ | 183,274,307,584^ | 1,466,127,351,808^ | 11,728,481,943,552^ | 93,826,781,806,592^ | 750,605,664,518,144^ | 6,004,828,136,275,968^ | 48,038,487,651,254,272 | 384,307,626,332,127,232 |
| 3 | 144^ | 3,483^ | 90,396^ | 2,407,887^ | 64,717,704^ | 1,744,720,803^ | 47,083,546,836^ | 1,271,040,530,967^ | 34,316,157,233,664^ | 926,518,811,386,923^ | 25,015,851,002,148,876^ | — | — | — | — | — | — | — | — |
| 5 | 1,375^ | 159,375^ | 19,609,375^ | 2,443,359,375^ | 305,224,609,375^ | 38,148,193,359,375^ | 4,768,402,099,609,375^ | — | — | — | — | — | — | — | — | — | — | — | — |
| 7 | 6,076^ | 1,990,429^ | 678,717,081^ | 232,569,366,743^ | 79,760,841,208,636^ | 27,357,428,724,361,309^ | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 11 | 47,190^ | 61,375,072^ | 81,534,323,464^ | 108,502,034,795,770^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 13 | 99,710^ | 215,407,062^ | 472,708,712,606^ | 1,038,436,628,063,094^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 17 | 338,130^ | 1,642,523,986^ | 8,064,313,527,762^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 19 | 555,940^ | 3,776,181,296^ | 25,887,466,479,060^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 23 | 1,296,050^ | 15,667,458,067^ | 190,564,823,479,032^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 29 | 3,741,609^ | 90,969,067,658^ | 2,218,287,697,118,362^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 31 | 5,053,899^ | 150,087,862,357^ | 4,470,713,705,180,443^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 37 | 11,288,774^ | 569,651,235,950^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 41 | 17,862,306^ | 1,228,544,806,926^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 43 | 22,143,624^ | 1,756,432,945,357^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 47 | 32,845,621^ | 3,401,742,737,444^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 53 | 55,899,100^ | 8,301,622,402,986^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 59 | 90,659,164^ | 18,582,530,492,106^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 61 | 105,776,867^ | 23,964,090,840,139^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 67 | 161,478,308^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 71 | 212,599,134^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 73 | 240,662,969^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 79 | 342,225,235^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 83 | 427,503,784^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 89 | 588,102,566^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 97 | 859,201,653^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 101 | 1,041,542,502^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 103 | 1,125,933,170^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 107 | 1,336,613,505^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 109 | 1,466,091,638^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 113 | 1,709,437,106^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 127 | 2,874,865,218^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 131 | 3,356,485,668^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 137 | 4,045,883,178^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 139 | 4,326,551,530^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 149 | 5,917,676,550^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 151 | 6,283,408,376^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 157 | 7,583,733,181^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 163 | 8,854,278,664^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 167 | 9,995,138,710^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 173 | 11,648,845,664^ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

Notes:
- **100 fields are proved.** Every one matched with the first polynomial the solver could
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
- **41⁵, 43⁵, 47⁵, 53⁵, 59⁵, 127³, 131³, 137³, 139³, 149³, 151³, 157³, 163³ and 3²¹** (2026-10-08/10) and **2³³, 2³⁵, 2³⁷, 2³⁹, 3²³** were started by the
  hourly "quickest field not yet calculated" rule (`campaigns/next_field.py`). 3²¹ (restarted with
  4096-cell tiles at Zooey's request) matched with the wide matcher; the others on a single GPU in
  minutes. 2³³, 2³⁵, 2³⁷ and 2³⁹ had DP values only (no `^`); 3²³ and then 2³³ were matched later (below):
  - **2³³ (2026-10-29):** matched with the wide matcher after it learned p = 2 (one extra payload bit
    while running, packed down when complete) and r > 31. Run **standalone on purdin's RTX 3080**
    (the leader was offline): 24 min of matching, 6 min of independent verification
    (`kh_verify_wide`, full matching of 8,589,934,592 requests), certificate 17,179,869,299 bytes,
    SHA-256 `480a03bf500ce31fd3fb7d74150e1e67442c672492c73cdc3c1437ab0c53164e`, kept at
    `~/king_hamming_archive/2_33/` on purdin. It is not in the leader's database, so
    `campaigns/result_table.py` would not show its `^`; the cell above was set by hand.
  - **2³⁵ (2026-10-29):** the same way on purdin: matching 5,190 s (1 h 27 min), independent
    verification 2,750 s (46 min), full matching of 34,359,738,368 requests, certificate
    73,014,444,151 bytes, SHA-256 `7a61a3bdce7905251c15a7cca55c789223e0088a1f0686483d7fd4175111f830`,
    kept at `~/king_hamming_archive/2_35/` on purdin. Like 2³³ it is not in the leader's database, so
    its `^` was set by hand.
  - **2³⁷ and 2³⁹:** still DP values only. 2³⁷ needs about 288 GiB of disk and 2³⁹ about 1.2 TiB. Before 2026-10-29 no matcher took p = 2 past F = 65,534 (the wide matcher refused
    p = 2, since F is a power of two). They are the first fields with r above 31; the DP's old
    cap of 31 had no width reason and was lifted on 2026-10-08.
  - **3²³** (2026-10-14/16): its certificate (197 GiB) wouldn't fit merlin's free disk while 7¹³'s
    filled the second drive. 7¹³'s certificate was moved to gawain (the table checks it there over
    ssh, `campaigns/remote_certificates.py`), and the wide matcher ran 38.7 hours on merlin's GPU
    (2,235 phases), then two verifications (the matcher's own, about 6 h, and the feeder's
    `kh_verify_wide`, about 5 h). Matched and verified; M(3²³ + 1, 3²³) ≥ 25,015,851,002,148,876.
- **107³, 109³ and 113³** were cancelled before their DP finished in September; Zooey retried
  them from the dashboard on 2026-10-06, and all three were matched and verified by 2026-10-07 08:00.

Regenerate this table from the live leader database with
`python3 campaigns/result_table.py` (read-only; it re-checks every certificate's hashes and
header, about a minute).
