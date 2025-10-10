#!/usr/bin/env pypy3


import itertools as it
from collections import defaultdict
# from functools import cache
from copy import deepcopy


ROWT = list(filter(None, '''
L L L L 5 6 H H
L L L L 5 6 H H
L L L L H 5 H H
L L L L 7 5 H H
L L L L H 4 H H
L L L L 7 4 H H
'''.replace(' ', '').split('\n')))

USED = []
for t in ROWT:
  row = set()
  for e in t:
    try:
      row.add(int(e))
    except:
      pass
  USED.append(row)

# L L L L L H H H
# L L L L H L H H
# L L L L H H L H
# L L L L H H H L

# tmp, ROWT = ROWT, []
# for row in tmp:
#   for _ in range(10):
#     ROWT.append(row)


def separated(u,v,d):
  for x,y in zip(u,v):
    if abs(x-y) >= d:
      return True
  return False


def main(T):
  sofar = []
  score = 0
  best = None
  def recur(depth=0):
    nonlocal score
    nonlocal best
    if best is None or len(sofar) > len(best):
      best = deepcopy(sofar)
      print()
      print('---')
      print('New best:', len(best))
      for row in best:
        print('  -', ' '.join(map(str, row)))
    if depth >= len(T):
      yield sofar
      return

    yield from recur(depth+1)

    # for h in it.permutations(list(range(6,9))):
    for h in it.permutations(list({6,7,8} - USED[depth])):
    #  for l in it.permutations(list(range(1,6))):
     for l in it.permutations(list({1,2,3,4,5} - USED[depth])):
      hs = iter(h)
      ls = iter(l)
      u = []
      for e in T[depth]:
        if e == 'L':
          u.append(next(ls))
        elif e == 'H':
          u.append(next(hs))
        else:
          u.append(int(e))
      good = True
      for v in sofar:
        if not separated(u,v,3):
          good=False
          break
    
      if not good:
        continue

      sofar.append(u)
      yield from recur(depth+1)
      sofar.pop()

  yield from recur()

for best in main(ROWT):
  # print('main:', best)
  pass


# for i in range(3, len(ROWT)):
#   b = float('-inf')
#   # print(ROWT[:i])
#   for best, ret in main(ROWT[:i]):
#     b = max(b, best or float('-inf'))
#     # for row in ret:
#     #   print(' '.join(map(str, row)))
#   print(i, best)
