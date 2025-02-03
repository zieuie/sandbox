# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting
# And permuting the whole HIGH(i) instead of just transposing

from copy import deepcopy
import random
import itertools as it
from collections import Counter
from datetime import datetime


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
    # if abs(a-b) >= d:
      return True
  return False


def asdf(pa, d):
  ret = []
  c = Counter()
  for vx, v in enumerate(pa):
    for ux in range(vx):
      u = pa[ux]
      separated = False
      for a,b in zip(u,v):
        if abs(a-b) >= d:
          separated = True
          break
      if not separated:
        ret.append((ux,vx))
        c.update([ux])
        c.update([vx])
  # return ret
  return c


def load_pa():
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def dumb_pa(n, k):
  m = n-k
  sr = set(range(n))
  A = []
  H = list(it.combinations(list(range(n)), k))
  L = [sorted(sr-set(e)) for e in H]
  for ps in H:
    highs = tuple(range(m))
    lows = tuple(range(m, n))
    h, l = 0, 0
    row = []
    for i in range(n):
      if i in ps:
        row.append(highs[h])
        h += 1
      else:
        row.append(lows[l])
        l += 1
    A.append(row)
  return A, H, L


# Find the best permutation of A[i]
def imp(A, start, s, i, d):
  bestw = 0
  end = None

  for target in it.permutations(start):
    pot = [e for e in A[i]]
    for u,v in zip(start, target):
      pot[u] = A[i][v]

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += len(s[x])

    if w > bestw or (w == bestw and random.random() < .5):
      bestw, end = w, target

  return bestw, start, end


def score_pa(A, d):
  s = [[] for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].append(vx)
        s[vx].append(ux)
  ret = 0
  for si in s:
    if len(si) == len(A)-1:
      ret += 10**10
    else:
      ret += len(si)
  return ret


def disturb(A, H, L, d):
  s = [[] for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].append(vx)
        s[vx].append(ux)
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  # for _ in range(random.randrange(1, 4)):
  for _ in range(1):
    i = random.randrange(10)
    i = q[i][1]

    ps = [e for e in H[i]]
    qs = [e for e in H[i]]
    random.shuffle(qs)
    ret = [e for e in A[i]]
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]

    ps = [e for e in L[i]]
    qs = [e for e in L[i]]
    random.shuffle(qs)
    ret = [e for e in A[i]]
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]
  A[i] = ret


def main(n, k, d):
  global full_metric
  # Step 1 - Make a dumb PA
  A, H, L = dumb_pa(n, k)
  try:
    A = load_pa()
  except FileNotFoundError:
    pass

  best_coverage = 0
  best_score = float('inf')
  best_pa = A
  count_disturbs = 0
  try:
   for qwer in it.count():
    if True:
    # if (qwer % 100 == 0) or (best_score < 20):
      disagreements = asdf(A, d)
      w = sum(disagreements.values())
      if w < best_score:
        best_score = w
        best_coverage = len(disagreements)
        best_pa = deepcopy(A)
      print(datetime.now(), 'Iteration:', qwer, 'Uncovered:', len(disagreements), 'Disagreements:', sum(disagreements.values()), 'Best score:', best_score, 'Best:', len(A) - best_coverage, 'of', len(A), 'Disturbances:', count_disturbs) # , disagreements)

      if 0 == len(disagreements):
        return A

    # Step 2 - Compute the separations of each row
    s = [[] for _ in A]
    for vx in range(len(A)):
      for ux in range(vx):
        if ux != vx and separated(A[ux], A[vx], d):
          s[ux].append(vx)
          s[vx].append(ux)

    # Step 3 - Sort the separations and indices
    q = []
    for idx, si in enumerate(s):
      if len(si) == len(A)-1:
        continue
      score = 0
      for x in si:
        score += len(s[x])
      q.append((score, idx))
    q = sorted(q)

    # Step 4 - Find the least separated row...
    for si, i in q:
      # Step 5 - ...and find the best improved transposition
      # separations, one, two = step_five(A, H, s, i, d)  # Try the highs
      separations, one, two = imp(A, H[i], s, i, d)  # Try the highs
      if separations <= si:
        separations, one, two = imp(A, L[i], s, i, d)  # Try the lows

      # We found a good transposition
      if separations > si:
        nex = [e for e in A[i]]
        for u,v in zip(one,two):
          nex[u] = A[i][v]
          # nex[v] = A[i][u]
        A[i] = nex
        break
    else:
      # There's nothing to do!
      # print ('Disturbing!')
      disturb(A, H, L, d)
      count_disturbs += 1
      # full_metric = not full_metric
      # return A
  except KeyboardInterrupt:
    return best_pa


if __name__ == '__main__':
  # The original PA is size m
  filename = 'dump6.txt'
  # pa = main(10, 5, 5)
  pa = main(12, 6, 6)
  with open(filename, 'w+') as f:
    disagreements = asdf(pa, 6)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

