
import itertools as it
from random import shuffle

def fillings(ps):
  for pps in it.permutations(ps):
    ret = [0]*9
    for p,e in zip(pps, (6,7,8)):
      ret[p] = e
    i = 0
    r = list(range(9))
    shuffle(r)
    for x in r:
      if not ret[x]:
        ret[x] = i
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

merp = list(it.combinations(list(range(9)), 3))
def recur(sofar):
  if len(sofar) >= len(merp):
    yield sofar
    return

  ps = merp[len(sofar)]
  for f in fillings(ps):
    if block_separated(sofar, f):
      yield from recur(sofar + [f])

for pa in recur([]):
  print()
  print('-'*10)
  for row in pa:
    print(row)
  input()