#!/usr/bin/env python3
"""Long-running, resumable, integer-certified search for Corollary 8 wins.

For k >= 1 and 1 <= d < k put N = 2*k+d-1.  Corollary 8 gives

  P(N,d) >= L(k,d)^2 * (1 + A/(2*e*S)),

where A and S are integers computed by :func:`f_parts`.  Klove et al. give an
integer bound K(N,d).  We test the strict inequality without logarithms.

If L^2 >= K it is immediate.  Otherwise it is equivalent to

  A*L^2 / (2*S*(K-L^2)) > e.

The last comparison is certified using rational lower and upper bounds from
the factorial series for e, with every operation performed on Python ints.

The search grows k one row at a time, closes the known lower bounds under
Theorem 4, and checkpoints after every completed k.  SIGINT/SIGTERM also cause
a clean checkpoint.  JSONL output is append-only and can be inspected while
the process is running.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pickle
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


TABLE2 = {
    (4,3): 3,
    (5,3): 10, (5,4): 3,
    (6,3): 20, (6,4): 10, (6,5): 3,
    (7,3): 105, (7,4): 35, (7,5): 10, (7,6): 3,
    (8,3): 503, (8,4): 70, (8,5): 35, (8,6): 10, (8,7): 3,
    (9,3): 1680, (9,4): 369, (9,5): 119, (9,6): 35, (9,7): 10, (9,8): 3,
    (10,3): 10594, (10,4): 1336, (10,5): 252, (10,6): 119, (10,7): 35, (10,8): 10,
    (11,3): 53549, (11,4): 6397, (11,5): 998, (11,6): 391, (11,7): 119, (11,8): 35,
    (12,3): 317728, (12,4): 34650, (12,5): 4355, (12,6): 924, (12,7): 391, (12,8): 120,
    (13,3): 1642473, (13,4): 138600, (13,5): 17049, (13,6): 3254, (13,7): 978, (13,8): 391,
    (14,3): 11081916, (14,4): 647420, (14,5): 81888, (14,6): 10709, (14,7): 3432, (14,8): 1089,
    (15,3): 55409580, (15,4): 3887796, (15,5): 392033, (15,6): 50283, (15,7): 10296, (15,8): 3432,
    (16,3): 332457480, (16,4): 15551184, (16,5): 1898103, (16,6): 250867, (16,7): 37017, (16,8): 12870,
}


def f_parts(N: int, k: int) -> tuple[int, int]:
    A = math.comb(N, 2*k) * math.comb(2*k, k)
    S = 0
    for i in range(k + 1):
        r = k - i
        if r <= N - 2*k and k <= N - 2*k + i:
            S += (math.comb(k, i) * math.comb(N - 2*k, r)
                  * math.comb(N - 2*k + i, k))
    if not S:
        raise ArithmeticError(f"zero denominator for N={N}, k={k}")
    return A, S


def klove(N: int, d: int) -> int:
    if d <= 0:
        raise ValueError("d must be positive")
    if d >= N:
        return 1
    a, b = divmod(N, d)
    return math.factorial(a + 1) ** b * math.factorial(a) ** (d - b)


def theorem4_compatible(n1: int, d1: int, n2: int, d2: int) -> bool:
    """Theorem 4 compatibility, using no floating-point division."""
    if min(n1, d1, n2, d2) <= 0:
        return False
    lo1, hi1 = (n1 - 1) // d1, n1 // d1
    lo2, hi2 = (n2 - 1) // d2, n2 // d2
    return max(0, lo1, lo2) <= min(hi1, hi2)


def compare_rational_to_e(p: int, q: int) -> tuple[int, int]:
    """Return (-1, terms), (1, terms) according as p/q < e or > e.

    After m terms, s_m=sum_{j=0}^m 1/j! and
       s_m < e < s_m + 1/(m*m!)  (m >= 1).
    Since e is irrational, a rational p/q cannot equal it and refinement must
    terminate.  All bound comparisons are integer cross-products.
    """
    if q <= 0:
        raise ValueError("positive denominator required")
    fact = 1
    numer = 2  # s_1 = 2/1
    m = 1
    while True:
        # p/q <= s_m = numer/fact
        if p * fact <= q * numer:
            return -1, m
        # p/q >= s_m + 1/(m*m!) = (m*numer+1)/(m*fact)
        if p * (m * fact) >= q * (m * numer + 1):
            return 1, m
        m += 1
        numer = numer * m + 1
        fact *= m


def randomized_beats(L: int, A: int, S: int, K: int) -> tuple[bool, str, int]:
    L2 = L * L
    if L2 >= K:
        return True, "inner-square-alone", 0
    cmp, terms = compare_rational_to_e(A * L2, 2 * S * (K - L2))
    return cmp > 0, "certified-e-series", terms


def rational_margin(L: int, A: int, S: int, K: int) -> tuple[int, int]:
    """Return Q=L^2*(1+A/(2eS))/K as symbolic integer pair around e.

    The reported pair (p,q) means the exact ratio is
       L^2*(2*e*S+A)/(2*e*S*K).
    p/q is instead the decisive threshold A*L^2/(2*S*(K-L^2)) when L^2<K.
    This is useful for ranking near misses without floating point: larger p/q
    is closer to or farther above e.
    """
    L2 = L * L
    if L2 >= K:
        return 1, 0  # positive infinity sentinel
    return A * L2, 2 * S * (K - L2)


@dataclass
class State:
    max_k: int
    bounds: list[list[int]]
    sources: list[list[str | None]]
    tested: int = 0
    hits: int = 0
    started: float = 0.0
    use_table: bool = True
    min_d: int = 3


def initial_state(use_table: bool, min_d: int) -> State:
    return State(0, [[0]], [[None]], started=time.time(),
                 use_table=use_table, min_d=min_d)


def extend_one(st: State, use_table: bool) -> None:
    n = st.max_k + 1
    row = [0] * (n + 1)
    source: list[str | None] = [None] * (n + 1)
    if n >= 2:
        row[1], source[1] = math.factorial(n), "exact d=1"
    if n >= 3:
        row[2] = math.factorial(n) // (2 ** (n // 2))
        source[2] = "exact d=2"
    for d in range(3, n):
        row[d], source[d] = klove(n, d), "Klove"
        t = TABLE2.get((n, d)) if use_table else None
        if t is not None and t > row[d]:
            row[d], source[d] = t, f"Table 2: {t}"

    # Components always have smaller n, hence are already final DP rows.
    for d in range(2, n):
        best, best_source = row[d], source[d]
        for d1 in range(1, d):
            d2 = d - d1
            for n1 in range(1, n):
                n2 = n - n1
                if not theorem4_compatible(n1, d1, n2, d2):
                    continue
                v1 = st.bounds[n1][d1] if d1 < n1 else 1
                v2 = st.bounds[n2][d2] if d2 < n2 else 1
                value = v1 * v2
                if value > best:
                    best = value
                    best_source = f"Thm 4: ({n1},{d1}) x ({n2},{d2})"
        row[d], source[d] = best, best_source
    st.bounds.append(row)
    st.sources.append(source)
    st.max_k = n


def atomic_pickle(path: Path, obj: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_json(path: Path, obj: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def ratio_order_key(p: int, q: int) -> tuple[int, int]:
    # Only a cheap display hint; final comparisons never use this key.
    return (p.bit_length() - q.bit_length(), (p << max(0, q.bit_length()-p.bit_length())) // q)


def candidate_ds(k: int, min_d: int) -> list[int]:
    """Fruitful-first order: manuscript table distances, then balanced d, then rest.

    Every admissible distance is still tested exactly once.  Table-supported
    small distances are cheapest and most likely to exploit a known improved
    inner bound.  Remaining distances are ordered near k/2 first, a useful
    empirical compromise between a strong F factor and a strong inner bound.
    """
    ds = list(range(min_d, k))
    return sorted(ds, key=lambda d: (0 if (k, d) in TABLE2 else 1,
                                     abs(2*d-k), d))


def emit_candidate(f, st: State, k: int, d: int) -> dict:
    N = 2*k + d - 1
    A, S = f_parts(N, k)
    L, K = st.bounds[k][d], klove(N, d)
    win, method, terms = randomized_beats(L, A, S, K)
    p, q = rational_margin(L, A, S, K)
    rec = {
        "N": N, "k": k, "d": d, "win": win,
        "inner_L": str(L), "klove_K": str(K),
        "F_A": str(A), "F_S": str(S),
        "source": st.sources[k][d], "certificate": method,
        "e_series_terms": terms,
        "threshold_num": str(p), "threshold_den": str(q),
    }
    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    f.flush()
    st.tested += 1
    st.hits += int(win)
    return rec


def write_hits_csv(jsonl: Path, csv_path: Path) -> None:
    fields = ["N", "k", "d", "inner_L", "klove_K", "F_A", "F_S",
              "source", "certificate", "e_series_terms"]
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with jsonl.open(encoding="utf-8") as src, tmp.open("w", newline="", encoding="utf-8") as dst:
        out = csv.DictWriter(dst, fieldnames=fields)
        out.writeheader()
        for line in src:
            rec = json.loads(line)
            if rec["win"]:
                out.writerow({x: rec[x] for x in fields})
    os.replace(tmp, csv_path)


def discard_uncheckpointed_records(jsonl: Path, completed_k: int) -> None:
    """Remove a partial row left by a kill/power loss before its checkpoint."""
    if not jsonl.exists():
        return
    tmp = jsonl.with_suffix(jsonl.suffix + ".tmp")
    with jsonl.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        for line in src:
            if json.loads(line)["k"] <= completed_k:
                dst.write(line)
    os.replace(tmp, jsonl)


def main() -> int:
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--output-dir", type=Path, default=Path("f_search_output"))
    ap.add_argument("--max-k", type=int, default=500,
                    help="stop after this inner-bound row; use 0 for no fixed limit")
    ap.add_argument("--hours", type=float, default=11.5,
                    help="wall-clock limit; use 0 for no time limit")
    ap.add_argument("--min-d", type=int, default=3)
    ap.add_argument("--no-table", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="refuse to reuse an old checkpoint")
    args = ap.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / "checkpoint.pkl"
    jsonl = out / "candidates.jsonl"
    summary = out / "summary.json"
    hits_csv = out / "improvements.csv"

    if args.fresh and (checkpoint.exists() or jsonl.exists()):
        ap.error(f"--fresh requested but prior output exists in {out}")
    if checkpoint.exists():
        with checkpoint.open("rb") as f:
            st: State = pickle.load(f)
        if st.use_table != (not args.no_table) or st.min_d != args.min_d:
            ap.error("checkpoint settings differ from --min-d/--no-table; use a new output directory")
        discard_uncheckpointed_records(jsonl, st.max_k)
        mode = "a"
        print(f"Resuming after k={st.max_k}; {st.tested} candidates already tested.")
    else:
        st, mode = initial_state(not args.no_table, args.min_d), "w"

    stop = False
    def request_stop(signum, frame):
        nonlocal stop
        stop = True
        print(f"\nReceived signal {signum}; stopping after the current k row.", flush=True)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    deadline = time.monotonic() + args.hours * 3600 if args.hours else None

    with jsonl.open(mode, encoding="utf-8", buffering=1) as records:
        while not stop and (not args.max_k or st.max_k < args.max_k):
            if deadline is not None and time.monotonic() >= deadline:
                break
            tick = time.monotonic()
            extend_one(st, not args.no_table)
            k = st.max_k
            new_hits = 0
            if k > args.min_d:
                for d in candidate_ds(k, args.min_d):
                    new_hits += int(emit_candidate(records, st, k, d)["win"])
            atomic_pickle(checkpoint, st)
            atomic_json(summary, {
                "completed_through_k": k, "tested": st.tested, "improvements": st.hits,
                "last_row_seconds": time.monotonic()-tick, "table2_used": not args.no_table,
                "min_d": args.min_d, "output_directory": str(out),
            })
            print(f"k={k:4d}  tested={st.tested:8d}  hits={st.hits:6d} "
                  f"(+{new_hits})  row={time.monotonic()-tick:.3f}s", flush=True)

    atomic_pickle(checkpoint, st)
    write_hits_csv(jsonl, hits_csv)
    atomic_json(summary, {
        "completed_through_k": st.max_k, "tested": st.tested, "improvements": st.hits,
        "table2_used": not args.no_table, "min_d": args.min_d,
        "stopped_cleanly": True, "output_directory": str(out),
    })
    print(f"Done. Review {hits_csv}, {summary}, and {jsonl}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
