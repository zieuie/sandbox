# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting

# Pretty bad at finishing off things

from copy import deepcopy
import random
import itertools as it
from collections import Counter
from datetime import datetime


def separated(u, v, d):
  for a,b in zip(u,v):
    if abs(a-b) >= d:
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


def random_pa(n, k):
  m = n-k
  sr = set(range(n))
  A = []
  H = list(it.combinations(list(range(n)), k))
  L = [sorted(sr-set(e)) for e in H]
  for ps in H:
    highs = list(range(m))
    lows = list(range(m, n))
    random.shuffle(highs)
    random.shuffle(lows)
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


full_metric = False
# Find the best transposition of A[i]
def step_five(A, H, s, i, d):
  bests, bestj, bestk = 0, 0, 0
  bestw = 0
  for kx, k in enumerate(H[i]):
    for jx in range(kx):
      j = H[i][jx]
      pot = [e for e in A[i]]
      pot[j], pot[k] = pot[k], pot[j]
      news = 0
      for x, e in enumerate(A):
        if x != i and separated(pot, e, d):
          # news += 1
          news += len(s[x])

      if full_metric:
        # print ('w')
        w = score_pa(A[:i] + [pot] + A[i+1:], d)
      else:
        # print ('e')
        w = news
      if w > bestw or (w == bestw and random.random() < .5):
      # if news > bests:
        bests, bestj, bestk = news, j, k
        bestw = w

  return bests, bestj, bestk


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


def disturb(A, H, d):

  s = [[] for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].append(vx)
        s[vx].append(ux)
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  i = random.randrange(10)
  i = q[i][1]

  ps = [e for e in H[i]]
  qs = [e for e in H[i]]
  random.shuffle(qs)
  ret = [e for e in A[i]]
  for p,q in zip(ps,qs):
    ret[p] = A[i][q]
  A[i] = ret


def main(n, k, d):
  global full_metric
  # Step 1 - Make a dumb PA
  A, H, L = dumb_pa(n, k)
  # A, H, L = random_pa(n, k)
  best_score = score_pa(A, d)
  try:
    pot = load_pa()
    pot_score = score_pa(A, d)
    if pot_score > best_score:
      best_score, A = pot_score, pot
  except FileNotFoundError:
    pass

  # while True:
  best_pa = A
  try:
   for qwer in it.count():
    if qwer % 100 == 0:
      w = score_pa(A, d)
      if w > best_score:
        best_score = w
        best_pa = deepcopy(A)
      disagreements = asdf(A, d)
      print(datetime.now(), 'Iteration:', qwer, 'Score:', w, 'Uncovered:', len(disagreements), 'Best score:', best_score, 'Best:', best_score//(10**10), 'of', len(A)) # , disagreements)
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
      separations, one, two = step_five(A, H, s, i, d)  # Try the highs
      if separations <= si:
        separations, one, two = step_five(A, L, s, i, d)  # Try the lows

      # We found a good transposition
      if separations > si:
        A[i][one], A[i][two] = A[i][two], A[i][one]
        break
    else:
      # There's nothing to do!
      # print ('Disturbing!')
      disturb(A, H, d)
      # full_metric = not full_metric
      # return A
  except KeyboardInterrupt:
    return best_pa


if __name__ == '__main__':
  # The original PA is size m
  filename = 'dump.txt'
  # pa = main(10, 5, 5)
  pa = main(12, 6, 6)
  with open(filename, 'w+') as f:
    disagreements = asdf(pa, 6)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

