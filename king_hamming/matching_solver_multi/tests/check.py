#!/usr/bin/env python3
"""Compare isolated native owners with an independent small-graph matching oracle."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from dp_solver.artifacts import dimensions, encode_dp
from matching_solver.artifacts import field_cells, load_dp, requests, verify
from matching_solver.polynomials import first_primitive


def maximum(dp, poly):
    cells = field_cells(dp["p"], dp["r"], poly)
    graph = []
    for coset, prefix, suffix in requests(dp):
        start = (prefix * dp["f"] + suffix) * dp["f"]
        graph.append([0 if z == 0 else 1 + (z - 1 - coset) % (dp["q"] - 1)
                      for z in cells[start:start+dp["f"]]])
    mates = {}
    def visit(u, seen):
        for v in graph[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in mates or visit(mates[v], seen):
                mates[v] = u
                return True
        return False
    return sum(visit(u, set()) for u in range(len(graph)))


def main():
    with tempfile.TemporaryDirectory(prefix="kh-multi-check-") as directory:
        temporary = Path(directory)
        cases = [ROOT.parent / "examples" / f"{name}.khdp" for name in ("3_3", "5_3", "7_5")]
        rng = random.Random(1729)
        for index in range(6):
            p, r = (2, 3) if index < 3 else (3, 3)
            q, f, budget = dimensions(p, r)
            a, b, t = (rng.randint(1, p) for _ in range(3))
            repeat = rng.randint(1, budget // (max(a, b) * t))
            omega = len({(h*t-g) % p for h in range(b) for g in range(a)})
            dp = dict(p=p, r=r, q=q, f=f, budget=budget, theta=t*repeat*omega,
                      runs=[dict(a=a, b=b, t=t, repeat=repeat)])
            path = temporary / f"random-{index}.khdp"
            path.write_bytes(encode_dp(dp))
            cases.append(path)
        checks = 0
        for index, path in enumerate(cases):
            dp, digest = load_dp(path)
            poly = first_primitive(dp["p"], dp["r"], dp["q"])
            workers = min(2, len(os.sched_getaffinity(0)))
            directory = temporary / f"run-{index}"
            subprocess.run([sys.executable, str(ROOT / "run.py"), str(path), "--hosts",
                            ",".join(["local"] * workers), "--batch", "64" if dp["q"] < 1000 else "4096",
                            "--poly", ",".join(map(str, poly)), "--output-dir", str(directory), "--verify"],
                           check=True, stdout=subprocess.DEVNULL, timeout=60)
            report = json.loads((directory / "metrics.json").read_text())
            assert report["verified"]
            assert sum(s["owned_field_labels"] for s in report["owners"]) == dp["q"]
            assert sum(s["owned_left"] for s in report["owners"]) == report["owners"][0]["required"]
            if dp["q"] <= 125:
                assert report["owners"][0]["matched"] == maximum(dp, poly)
            # Independent verifier must reject a changed certificate.
            raw = bytearray((directory / "result.khmatch").read_bytes())
            raw[-1] ^= 1
            bad = directory / "bad.khmatch"
            bad.write_bytes(raw)
            try:
                verify(bad, dp, digest)
            except ValueError:
                pass
            else:
                raise AssertionError("corrupt certificate accepted")
            checks += 1
        print(f"{checks} partitioned matching cases passed; independent cardinalities, certificates and ownership checked")


if __name__ == "__main__":
    main()
