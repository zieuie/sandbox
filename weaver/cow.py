

import itertools as it
from lib import *


def distance(s, t):
  return max(abs(a-b) for a,b in zip(s,t))


n, d = 9, 3
B = load_pa(f'pa_{n-d}_choose_{d}_verified.txt')
k = 5  # the number of highest symbols we care about


# lut[(t)] = q
# For order t, use q as positions/symbols
lut = dict()
for r in B:
  t = []
  # p = []
  q = []
  for i,e in enumerate(r):
    if e >= n-d-d:
      # the unknown symbols
      t.append(None)
      # p.append(len(t))
      q.append(e)
    elif e >= n-d-k:
      # the known symbols
      t.append(e)

  print(r, t, q)
  lut[tuple(t)] = q
  # print(r, t, p, q)
  # lut[tuple(t)] = (p,q)

print()
print('Part two!')

# start filling
A = []
for hs in it.combinations(list(range(n)), d):
  for r in B:
    t = []
    s = []
    for i,e in enumerate(r):
      while len(s) in hs:
        t.append(None)
        s.append(None)
      if e >= n-k:
        t.append(e-d)
      s.append(e)

    print(r, s, t, hs)
    qs = lut[tuple(t)]
    for p,q in zip(hs, qs):
      s[p] = q+d
    print(s)
    # input()

#