import re
import itertools as it
from collections import defaultdict
# from functools import cache
from copy import deepcopy


blobs = []
A = []
with open('moo.txt', 'r') as f:
  for line in f:
    row = list(map(int, re.sub(r'[^\d]', ' ', line).strip().split()))
    if len(row) > 1:
      A.append(row)
    else:
      if A:
        blobs.append(A)
        A = []

if A:
  blobs.append(A)


## 6 vs 5 (7 vs 6)
# ROWT = list(filter(None, '''
# L L L L 4 5 H H
# L L L L 4 5 H H
# L L L L H 4 H H
# L L L L 6 4 H H
# L L L L H 3 H H
# L L L L 6 3 H H
# '''.replace(' ', '').split('\n')))

## 6 vs 6 (7 vs 7)

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
  used = []
  for t in T:
    row = set()
    for e in t:
      try:
        row.add(int(e))
      except:
        pass
    used.append(row)

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
      if len(sofar) >= len(T):
        yield sofar
      return

    yield from recur(depth+1)

    # for h in it.permutations(list(range(6,9))):
    for h in it.permutations(list({5,6,7} - used[depth])):
    #  for l in it.permutations(list(range(1,6))):
     for l in it.permutations(list({0,1,2,3,4} - used[depth])):
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

# for best in main(ROWT):
#   pass


def block_arrangement(V):
  for block in blobs:
    for perm in it.permutations(list(range(5))):
      good = True
      for v in V:
        found = False
        for row in block:
          u = [row[e] for e in perm]
          if u == v:
            found = True
            break
        if not found:
          good = False
          break
      if good:
        ret = []
        for row in block:
          u = [row[e] for e in perm]
          ret.append(u)
        yield ret

'''
  LLLLL7HH - a
  LLLL5L76 - b
  LLLL75L6 - c
  LLLL756L - d

  b,d is 65 (76)
  c,d is 66 (77)


  LLLL5376 - b
  LLLL5376 - b
  LLLL5476 - b
  LLLL5476 - b
  LLLL7546 - c
  LLLL7546 - c
  LLLL7564 - d
  LLLL7564 - d

'''


# L L L L 4 H H H
# L L L L 4 6 H H
# L L L L 6 4 H H
# L L L L 6 4 H H
ROWT = list(filter(None, '''
L L L L 5 3 7 6
L L L L 5 3 7 6
L L L L 5 4 7 6
L L L L H 4 7 H
L L L L 7 5 4 6
L L L L 7 H 4 H
L L L L 7 5 6 4
L L L L 7 H H 4
'''.replace(' ', '').split('\n')))

for way in main(ROWT):
  vv = [[e for e in row if e < 5] for row in way]

  q = []
  for arr in block_arrangement(vv[:4]):
    q.append(arr)
    break
  for arr in block_arrangement(vv[4:6]):
    q.append(arr)
    break
  for arr in block_arrangement(vv[6:8]):
    q.append(arr)
    break

  if len(q) == 3:
    print('###')
    for row in way:
      print(row)
    print('###')
    for b in q:
      print('---')
      for r in b:
        print(' '.join(map(str, r)))
    break


'''
L76
7L6
76L

'''