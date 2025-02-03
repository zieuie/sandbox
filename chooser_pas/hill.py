# Sudborough's January 31 Algorithm

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
          news += 1
      if news > bests:
        bests, bestj, bestk = news, j, k

  return bests, bestj, bestk


def main(n, k, d):
  # Step 1 - Make a dumb PA
  A, H, L = dumb_pa(n, k)

  while True:
    # Step 2 - Compute the separations of each row
    s = [[] for _ in A]
    for vx in range(len(A)):
      for ux in range(vx):
        if ux != vx and separated(A[ux], A[vx], d):
          s[ux].append(vx)
          s[vx].append(ux)

    # Step 3 - Sort the separations and indices
    q = sorted([(len(si),idx) for idx,si in enumerate(s) if len(si) != len(A)-1])
    w = sum( 1 for si in s if len(si) == len(A)-1 )

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
      print ('Done')
      return A


if __name__ == '__main__':
  # The original PA is size m
  pa = main(10, 5, 5)
  for row in pa:
    print(row)
  print()

  disagreements = asdf(pa, 5)
  print('Disagreements:', len(disagreements), disagreements)
