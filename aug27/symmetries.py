# Trying Sergey's idea about symmetries
# Namely, experimenting with making a PA
# with only the group number
# and filling it in separately

# Hal didn't like it so I stopped

import itertools as it
from collections import defaultdict, Counter


def separated(u,v,d):
  for x,y in zip(u,v):
    if abs(x-y) < d:
      return False
  return True


def infills(u):
  sofar = []
  def recur():
    if len(sofar) >= n:
      yield sofar
      return
    c = u[len(sofar)]
    for x in range(c*d, (c+1)*d):
      if x in sofar:
        continue
      sofar.append(x)
      yield from recur()
      sofar.pop()
  yield from recur()


# ABCD -> CDAB
# sym = [2,3,0,1]
# sym = [0, 3, 2, 1]
# sym = [1,2,3,0]

# Hal's Symmetry
# syms = [
#   [0,1,2,3],
#   [1,2,3,0],
#   [2,3,0,1],
#   [3,0,1,2],
# ]

# Cyclic Shifts, sorta
# syms = [
  # [0,1,2,3],
  # [2,1,0,3],
  # [0,3,2,1],
  # [2,3,0,1],
# ]

# Angry symmetry
syms = [
  
]


# args
n, d = 8, 2
# n, d = 12, 3


# weave template
A = [[0]*d]
for x in range(1, n//d):
  B = []
  for a in A:
    for ps in it.combinations(list(range(len(a)+d)), d):
      nex = []
      l = 0
      for i in range(len(a)+d):
        if i in ps:
          nex.append(x)
        else:
          nex.append(a[l])
          l += 1
      B.append(nex)
  A = B


# remove redundant rows
B = defaultdict(set)
for vx, v in enumerate(A):
  for sym in syms:
    s = tuple(sym[e] for e in v)
    if s in B:
      B[s].add(vx)
      break
  else:
    B[tuple(v)].add(vx)

print(len(B), 'vs.', len(A))

print(Counter(map(len,B.values())))

u = list(B.keys())[0]

R = []
for sym in syms:
  s = tuple(sym[e] for e in u)
  R.append(s)

good, bad = 0, 0
for S in it.product(*map(infills, R)):
  fail = False
  for u in S:
    for v in S:
      if u != v and not separated(u,v,n):
        fail = True
  if fail:
    bad += 1
  else:
    good += 1

print(good, bad, good+bad)
