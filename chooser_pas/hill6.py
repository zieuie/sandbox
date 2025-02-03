# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting
# And permuting the whole HIGH(i) instead of just transposing, but choosing candidate permutations randomly
# And not measuring the whole PA's separation each time

from copy import deepcopy
import random
import itertools as it
from collections import Counter
from datetime import datetime


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
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
    lows = tuple(range(m))
    highs = tuple(range(m, n))
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
def random_good_permutation(A, start, i, d, old_score, loops=10):
  target = [e for e in start]
  for _ in range(loops):
    random.shuffle(target)
    pot = [e for e in A[i]]
    for u,v in zip(start, target):
      pot[u] = A[i][v]

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += 1

    if w > old_score:
      return w, start, target
  else:
    return 0, None, None


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


def disturb2(A, H, L, d):
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

    ret = [e for e in A[i]]

    ps = [e for e in H[i]]
    qs = [e for e in H[i]]
    random.shuffle(qs)
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]

    ps = [e for e in L[i]]
    qs = [e for e in L[i]]
    random.shuffle(qs)
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]

  A[i] = ret

def disturb(A, H, L, d):
  # Pick a random row that isn't fully separated
  shuf = list(range(len(A)))
  random.shuffle(shuf)
  for vx in shuf:
    for ux in range(len(A)):
      if ux != vx and not separated(A[ux], A[vx], d):
        i = vx
        break
    else:
      continue
    break

  ret = [e for e in A[i]]

  if random.random() < .5:
    # Permute the high symbols
    ps = [e for e in H[i]]
    qs = [e for e in H[i]]
  else:
    # Permute the low symbols
    ps = [e for e in L[i]]
    qs = [e for e in L[i]]

  ret = [e for e in A[i]]
  random.shuffle(qs)
  for u,v in zip(ps,qs):
    ret[u] = A[i][v]
  A[i] = ret


def main(n, k, d):
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
    # if (qwer % 1000 == 0) or (best_score < 3000):
    # if (qwer % 1000 == 0) or (best_score < 20):
      disagreements = asdf(A, d)
      w = sum(disagreements.values())
      if w < best_score:
        best_score = w
        best_coverage = len(disagreements)
        best_pa = deepcopy(A)
      print(datetime.now(), 'Iteration:', qwer, 'Uncovered:', len(disagreements), 'Disagreements:', sum(disagreements.values()), 'Best score:', best_score, 'Best coverage:', len(A) - best_coverage, 'of', len(A), 'Disturbances:', count_disturbs) # , disagreements)

      if 0 == len(disagreements):
        return A


    for i in random.choices(list(range(len(A))), k=10):
      score = 0
      for j in range(len(A)):
        if i != j and separated(A[i], A[j], d):
          score += 1

      separations, one, two = random_good_permutation(A, H[i], i, d, score, 10)
      if separations <= score:
        separations, one, two = random_good_permutation(A, L[i], i, d, score, 10)

      # We found a good transposition
      if separations > score:
        nex = [e for e in A[i]]
        for u,v in zip(one,two):
          nex[u] = A[i][v]
        A[i] = nex
        break
    else:
      disturb(A, H, L, d)
      count_disturbs += 1

  except KeyboardInterrupt:
    return best_pa


if __name__ == '__main__':
  from sys import argv
  n, d = int(argv[1]), int(argv[2])

  # The original PA is size m
  filename = f'pa_{n}_choose_{d}.txt'
  pa = main(n, d, d)
  with open(filename, 'w+') as f:
    disagreements = asdf(pa, d)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

