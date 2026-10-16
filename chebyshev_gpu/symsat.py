"""SAT search for symmetric template permutation arrays (n <= 2d).

Same objects as gpuclimb: rows are indexed by their template U (positions of
the values 0..d-1); the array is the orbit of one representative row per
template orbit under a group G acting freely on templates.  Only the
representatives get variables; image rows reuse them, permuted.

  x[j,i,v]   representative j has value v at position i (v < d iff i in U_j)
  s[j]       orbit j is used (subset mode; --active m asks for >= m orbits)
  z          position p separates a pair (|values| >= d there)

UNSAT means: no G-invariant array with that many orbits exists.

LNS repair (--fix FILE --free K): rows from FILE (e.g. a climber's best
array) fix every orbit that is not among the K freed ones; freed orbits are
the conflicting ones plus their most-conflicting neighbours.

usage: python3 symsat.py n d [--active m] [--group auto|trivial]
                         [--fix FILE --free K] [--solver kissat|cadical153]
                         [--timeout S] [--dimacs-only]
"""
import argparse, itertools, os, random, subprocess, sys, time
from pysat.formula import IDPool
from pysat.card import CardEnc, EncType

ap = argparse.ArgumentParser()
ap.add_argument('n', type=int); ap.add_argument('d', type=int)
ap.add_argument('--active', type=int, default=0)
ap.add_argument('--group', default='auto')
ap.add_argument('--fix'); ap.add_argument('--free', type=int, default=0)
ap.add_argument('--solver', default='kissat')
ap.add_argument('--timeout', type=int, default=0)
ap.add_argument('--dimacs-only', action='store_true')
ap.add_argument('--seed', type=int, default=1)
A = ap.parse_args()
n, d = A.n, A.d
assert n <= 2 * d
random.seed(A.seed)

# ---- group (same choice as gpuclimb) --------------------------------------
def groups():
    if A.group == 'trivial':
        yield 'trivial', [(0, list(range(n)))]; return
    if n == 2 * d:
        m = n - 1
        yield 'dihedral+reversal', [(s, [((-x if s else x) + b) % m for x in range(m)] + [m]) for s in (0, 1) for b in range(m)]
    yield 'Z_n', [(0, [(x + b) % n for x in range(n)]) for b in range(n)]
    yield 'Z_(n-1)', [(0, [(x + b) % (n - 1) for x in range(n - 1)] + [n - 1]) for b in range(n - 1)]
    yield 'trivial', [(0, list(range(n)))]

def act_mask(g, U):
    t, p = g
    out = frozenset(p[i] for i in U)
    return frozenset(range(n)) - out if t else out

templates = [frozenset(c) for c in itertools.combinations(range(n), d)]
for gname, G in groups():
    seen, reps, ok = {}, [], True
    for U in templates:
        if U in seen: continue
        orb = [act_mask(g, U) for g in G]
        if len(set(orb)) != len(G): ok = False; break
        for h, W in enumerate(orb): seen[W] = (len(reps), h)
        reps.append(U)
    if ok: break
NG, R = len(G), len(reps)
ginv = []
for t, p in G:
    q = [0] * n
    for i in range(n): q[p[i]] = i
    ginv.append(q)
print(f"P({n},{d}): group {gname}, |G|={NG}, reps={R}, rows={R*NG}", flush=True)

# ---- variables -------------------------------------------------------------
pool = IDPool(); cls = []
def allowed(j, i): return range(d) if i in reps[j] else range(d, n)
def x(j, i, v): return pool.id(('x', j, i, v))
def lit(k, h, p, w):
    """literal: image row (k,h) has value w at position p (None if impossible)."""
    t, _ = G[h]
    i = ginv[h][p]
    v = n - 1 - w if t else w
    return x(k, i, v) if (v < d) == (i in reps[k]) else None
for j in range(R):
    for i in range(n):
        cls += CardEnc.equals([x(j, i, v) for v in allowed(j, i)], 1, vpool=pool, encoding=EncType.seqcounter).clauses
    for v in range(n):
        pos = [i for i in range(n) if (v < d) == (i in reps[j])]
        cls += CardEnc.equals([x(j, i, v) for i in pos], 1, vpool=pool, encoding=EncType.seqcounter).clauses
subset = 0 < A.active < R
s = {j: pool.id(('s', j)) for j in range(R)} if subset else {}
if subset:
    cls += CardEnc.atleast([s[j] for j in range(R)], A.active, vpool=pool, encoding=EncType.seqcounter).clauses

# ---- fixing (LNS) ----------------------------------------------------------
fixed = {}
if A.fix:
    rows = [list(map(int, l.split())) for l in open(A.fix) if l.strip()]
    byU = {}
    for r in rows: byU[frozenset(i for i in range(n) if r[i] < d)] = r
    def image(h, r):
        t, p = G[h]; out = [0] * n
        for i in range(n): out[p[i]] = n - 1 - r[i] if t else r[i]
        return out
    present = {j: byU[reps[j]] for j in range(R) if reps[j] in byU}
    full = {(k, h): image(h, present[k]) for k in present for h in range(NG)}
    def sep(a, b): return any(abs(u - v) >= d for u, v in zip(a, b))
    conf = {j: 0 for j in present}
    for j in present:
        for (k, h), row in full.items():
            if (k, h) != (j, 0) and not sep(present[j], row): conf[j] += 1
    bad = [j for j in present if conf[j] > 0]
    # neighbours: orbits that most often come close to the bad ones (distance d .. d+1)
    score = {}
    for j in bad:
        for (k, h), row in full.items():
            if k in bad or k == j: continue
            if max(abs(u - v) for u, v in zip(present[j], row)) <= d: score[k] = score.get(k, 0) + 1
    extra = sorted(score, key=lambda k: -score[k])
    free = set(bad) | set(extra[:max(0, A.free - len(bad))])
    absent = [j for j in range(R) if j not in present]
    for j in present:
        if j in free: continue
        fixed[j] = present[j]
        for i in range(n): cls.append([x(j, i, present[j][i])])
        if subset: cls.append([s[j]])
    print(f"fix: {len(present)} orbits in file, {len(bad)} conflicting, {len(free)} freed, {len(absent)} absent (free to use)", flush=True)

# ---- separation ------------------------------------------------------------
def z_pos(j, k, h, p):
    """aux z: position p separates rep j from image row (k,h)."""
    z = pool.id(('z', j, k, h, p))
    for a in allowed(j, p):
        far = [l for b in range(n) if abs(a - b) >= d for l in [lit(k, h, p, b)] if l is not None]
        cls.append([-z, -x(j, p, a)] + far)
    return z
npairs = 0
for j in range(R):
    for k in range(j, R):
        if j in fixed and k in fixed: continue
        for h in range(NG):
            if k == j:
                if h == 0: continue
                hi = next(q for q in range(NG) if G[q][0] == G[h][0] and all(G[q][1][G[h][1][i]] == i for i in range(n)))
                if hi < h: continue  # (j, h.j) and (j, h^-1.j) are the same pair
            # positions where one is low and the other high can separate
            other_low = act_mask(G[h], reps[k])
            ps = [p for p in range(n) if (p in reps[j]) != (p in other_low)]
            clause = [z_pos(j, k, h, p) for p in ps]
            if subset: clause = [-s[j]] + ([-s[k]] if k != j else []) + clause
            cls.append(clause); npairs += 1
print(f"pairs={npairs} vars={pool.top} clauses={len(cls)}", flush=True)

# ---- solve -----------------------------------------------------------------
tag = f"{n}_{d}_{A.active or R}" + ("_lns" if A.fix else "")
cnf = f"sym_{tag}.cnf"
with open(cnf, 'w') as f:
    f.write(f"p cnf {pool.top} {len(cls)}\n")
    for c in cls: f.write(' '.join(map(str, c)) + ' 0\n')
if A.dimacs_only: print("wrote", cnf); sys.exit(0)
import gc
if A.solver == 'kissat':  # free the in-memory clauses before the solver runs
    ncls = len(cls); del cls; gc.collect()
t0 = time.time()
here = os.path.dirname(os.path.abspath(__file__))
if A.solver == 'kissat':
    cmd = [os.path.join(here, 'kissat'), '-q', cnf] + ([f'--time={A.timeout}'] if A.timeout else [])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    status = 'SAT' if 's SATISFIABLE' in out else 'UNSAT' if 's UNSATISFIABLE' in out else 'UNKNOWN'
    model = set(int(t) for l in out.splitlines() if l.startswith('v ') for t in l[2:].split() if int(t) > 0)
else:
    from pysat.solvers import Solver
    with Solver(name=A.solver, bootstrap_with=cls) as S:
        ok = S.solve(); status = 'SAT' if ok else 'UNSAT'
        model = set(l for l in S.get_model() if l > 0) if ok else set()
print(f"result: {status} in {time.time()-t0:.1f}s", flush=True)
os.remove(cnf)
if status != 'SAT': sys.exit(0 if status == 'UNSAT' else 2)

# ---- decode and verify -----------------------------------------------------
reprow = {}
for j in range(R):
    if subset and s[j] not in model: continue
    reprow[j] = [next(v for v in allowed(j, i) if x(j, i, v) in model) for i in range(n)]
arr = []
for j, r in reprow.items():
    for h, (t, p) in enumerate(G):
        out = [0] * n
        for i in range(n): out[p[i]] = n - 1 - r[i] if t else r[i]
        arr.append(out)
assert len(set(map(tuple, arr))) == len(arr) and all(sorted(r) == list(range(n)) for r in arr)
mind = min(max(abs(a - b) for a, b in zip(u, v)) for u, v in itertools.combinations(arr, 2))
assert mind >= d, mind
fn = f"pa_{n}_{d}_sat_{len(arr)}.txt"
open(fn, 'w').write(''.join(' '.join(map(str, r)) + '\n' for r in arr))
print(f"verified: {len(arr)} rows, min distance {mind}; wrote {fn}", flush=True)
