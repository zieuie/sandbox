# Sudborough's January 31 Algorithm with a tweaked score

import itertools as it
from collections import Counter


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
      if w > bestw:
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
      ret += 10**6
    else:
      ret += len(si)
  return ret


def main(n, k, d):
  global full_metric
  # Step 1 - Make a dumb PA
  A, H, L = dumb_pa(n, k)

  # while True:
  for qwer in it.count():
    # if qwer % 100 == 0:
    if True:
      disagreements = asdf(A, 5)
      w = score_pa(A, d)
      print('Disagreements:', w, len(disagreements)) #, disagreements)

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
      # score = len(si)
      score = 0
      for x in si:
        # score += 1
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
      # print ('Switching')
      # full_metric = not full_metric
      return A


if __name__ == '__main__':
  # The original PA is size m
  pa = main(10, 5, 5)
  # for row in pa:
  #   print(row)
  # print()

  disagreements = asdf(pa, 5)
  print('Disagreements:', len(disagreements), disagreements)
