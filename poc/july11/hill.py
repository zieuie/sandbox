import itertools as it
from copy import deepcopy
from datetime import datetime

from lib import apply_permutation
import random


# if a symbol is -1, it isn't separated
def separated2(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if a>=0 and b>=0 and (a-b)**2 >= dd:
      return True
  return False


def verify2(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated2(pa[ux], pa[vx], d):
        return False
  return True


def init_separations(A, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated2(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, start, target, s, i, d):
  pot = apply_permutation(A[i], start, target)
  news = []
  adders = []
  subers = []
  for x, e in enumerate(A):
    if x == i:
      continue
    if separated2(pot, e, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news


def update_diffs(A, s, i, row, adders, subers, news):
  A[i] = row
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)


def yoink_columns(A, n, d):
  twists = n // d
  ret = [[] for _ in range(twists)]
  for row in A:
    buckets = [[] for _ in range(twists)]
    for i,e in enumerate(row):
      buckets[e//d].append(i)
    for r,b in zip(ret,buckets):
      r.append(b)
  return ret


def hill_climb_iter(A, n, d):
  N = len(A)
  s = init_separations(A, d)
  W = N * (N-1)
  # P = yoink_columns(A, n, d)

  # A = np.array(A)
  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_tweak = 0

  try:
    for it_count in it.count():
      score = W - sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == N-1)

      if score < best_score:
        best_score = score
        best_pa = deepcopy(A)
        best_s = deepcopy(s)

      if N == coverage:
        yield deepcopy(A)
        return

      force = False
      if score >= best_score and it_count - last_tweak > 10000:
        A, s, last_tweak, force = deepcopy(best_pa), deepcopy(best_s), it_count, True

      for tries in it.count():
        i = random.randrange(N)
        src = random.sample(list(range(n)), k=random.randrange(2, n+1))
        dst = deepcopy(src)
        random.shuffle(dst)

        adders, subers, news = eval_permutation(A, src, dst, s, i, d)
        if len(news) + len(adders) > 2*len(subers):
          # improvement, great!
          print('tries', tries)
          last_tweak = it_count
          break
        elif len(news) + len(adders) == 2*len(subers) and tries > 100 and not force:
          # wandering, sure!
          break
        elif tries > 100000 or force:
          # backtracking, maybe.
          print ('backtracking...')
          last_tweak = it_count
          break

      row = apply_permutation(A[i], src, dst)
      update_diffs(A, s, i, row, adders, subers, news)


  except KeyboardInterrupt:
    yield deepcopy(best_pa)
    pass


def hill_climb_driver(A,n,d):
  for pa in hill_climb_iter(A, n, d):
    if verify2(pa, d):
      return pa
