#!/usr/bin/env pypy3


import itertools as it
from collections import defaultdict
# from functools import cache
from copy import deepcopy


ROWT = list(filter(None, '''

L L L L H L H H
L L L L H H L H
L L L L H H H L

L L L H L H H L
L L L H H L H L
L L L H H H L L

L L H L H H L L
L L H H L H L L
L L H H H L L L

L H L H H L L L
L H H L H L L L
L H H H L L L L

H L H H L L L L
H H L H L L L L
H H H L L L L L

L H H L L L L H
H L H L L L L H
H H L L L L L H

H H L L L L H L
L H L L L L H H
H L L L L L H H

H L L L L H L H
H L L L L H H L
L L L L L H H H

'''.replace(' ', '').split('\n')))

# '''
# '''


cache = dict()
def pairval(u,v):
  auto = False
  top = None
  bottom = None
  for x,y in zip(u, v):
    if (x,y) == (8,'L') or (x,y) == ('L',8):
      auto = True
      break
    elif (x == 'L') and (y != 'L'):
      if bottom is None or y < bottom:
        bottom = y
    elif (x != 'L') and (y == 'L'):
      if top is None or x < top:
        top = x

  if not auto:
    case = tuple(sorted((top,bottom)))

  ret = None
  if auto:
    ret = 0
  elif case == (6,6):
    ret = -5
  elif case == (6,7):
    ret = -2
  elif case == (7,7):
    ret = -1
  else:
    print('Unknown Case: ', case)
    print(list(map(str, u)))
    print(list(map(str, v)))
    ret = input('Answer:')
    cache[case] = ret

  return ret


def main(T):
  sofar = []
  score = 0
  best = None
  ret = None
  def recur():
    nonlocal score
    nonlocal best
    nonlocal ret
    if len(sofar) >= len(T):
      if best is None or score > best:
        best = score
        ret = deepcopy(sofar)
        yield best, ret
      return

    for h in it.permutations(list(range(6,9))):
      nex = iter(h)
      row = [e if e == 'L' else next(nex) for e in T[len(sofar)]]
      diff = 0
      for u in sofar:
        diff += pairval(row, u)

      if best is not None and (score + diff < best):
        continue

      score += diff
      sofar.append(row)
      yield from recur()
      sofar.pop()
      score -= diff

  yield from recur()


for i in range(3, len(ROWT)):
  b = float('-inf')
  # print(ROWT[:i])
  for best, ret in main(ROWT[:i]):
    b = max(b, best or float('-inf'))
    # for row in ret:
    #   print(' '.join(map(str, row)))
  print(i, best)
