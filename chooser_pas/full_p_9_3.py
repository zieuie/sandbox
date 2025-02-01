
import itertools as it
from random import shuffle

smol = [
  [0, 1, 2, 3, 4, 5],
  [0, 1, 3, 4, 5, 2],
  [0, 1, 5, 3, 2, 4],
  [0, 3, 2, 4, 1, 5],
  [0, 3, 4, 1, 5, 2],
  [0, 3, 4, 5, 2, 1],
  [0, 4, 1, 2, 5, 3],
  [0, 4, 1, 5, 3, 2],
  [0, 4, 3, 1, 2, 5],
  [2, 1, 3, 0, 4, 5],
  [3, 0, 1, 4, 2, 5],
  [3, 0, 1, 4, 5, 2],
  [3, 0, 4, 1, 5, 2],
  [3, 0, 4, 2, 1, 5],
  [3, 0, 4, 5, 1, 2],
  [3, 2, 0, 1, 4, 5],
  [3, 4, 0, 1, 5, 2],
  [3, 4, 0, 2, 1, 5],
  [3, 4, 0, 5, 1, 2],
  [3, 4, 5, 0, 1, 2]
]

def fillings(ps, r):
  for pps in it.permutations(ps):
    ret = [0]*9
    for p,e in zip(pps, (6,7,8)):
      ret[p] = e
    i = 0
    for x in range(9):
      if not ret[x]:
        ret[x] = r[i]
        i += 1
    yield ret

def separated(a, b):
  for u,v in zip(a,b):
    if abs(u-v) >= 3:
      return True

def block_separated(A, x):
  for a in A:
    if not separated(a, x):
      return False
  return True

def birdshot(pa, row):
  def recur(sofar):
    if len(sofar) < 10:
      print(len(merp), len(sofar))
    # we tried all position tuples
    if len(sofar) >= len(merp):
      yield sofar
      return

    # try next position tuple
    ps = merp[len(sofar)]
    for f in fillings(ps, row):
      if block_separated(pa, f) and block_separated(sofar, f):
        yield from recur(sofar + [f])
  yield from recur([])

merp = list(it.combinations(list(range(9)), 3))

def recur(pre_pa, smol_idx):
  if smol_idx >= len(smol):
    yield pre_pa
    return

  for adjunct in birdshot(pre_pa, smol[smol_idx]):
    print('birdshot')
    yield from recur(pre_pa + adjunct, smol_idx+1)


# for pa in recur([], 0):
#   print()
#   print('-'*10)
#   for row in pa:
#     print(row)
#   break

for e in birdshot([], smol[0]):
  print(e, len(e))
  input()