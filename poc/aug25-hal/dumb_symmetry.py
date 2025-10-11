
# Let's just start over

import random
import itertools as it
from datetime import datetime
from collections import defaultdict
from time import time


### Utils

def infill(row, highs, ps):
  n = len(row) + len(highs)
  l, h = 0, 0
  new = []
  for i in range(n):
    if i in ps:
      new.append(highs[h])
      h += 1
    else:
      new.append(row[l])
      l += 1
  return new


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def symmetric_separated(u,v,n,d):
  # normie version
  return separated(u,v,d)

  # symmetric version
  cu = symmetry(u, n)
  cv = symmetry(v, n)
  return separated(u,v,d) and separated(cu,v,d) and separated(u,cv,d) and separated(cu,cv,d)


### Specific

def pull_groups(p, d):
  return tuple(e // d for e in p)


def symmetry(p, n):
  # normie version
  return p
  # bump half
  return [(e+n//2) % n for e in p]


def enweave(A, n, d):
  ret = []
  seen = set()
  highs = list(range(n-d, n))
  for row in A:
    for ps in it.combinations(list(range(n)), d):
      # random.shuffle(highs)
      u = infill(row, highs, ps)
      v = symmetry(u, n)
      gu = pull_groups(u, d)
      gv = pull_groups(v, d)
      if gu not in seen and gv not in seen:
        seen.add(gu)
        seen.add(gv)
        ret.append(u)
  return ret


def init_problems(A, n, d):
  s = [set() for _ in A]
  for vx, v in enumerate(A):
    for ux, u in enumerate(A):
      if ux != vx and not symmetric_separated(u, v, n, d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, n, d, umad, ux, upot):
  gain = []
  loss = []
  for vx, v in enumerate(A):
    if ux == vx:
      continue
    sep = symmetric_separated(upot, v, n, d)
    if sep and vx in umad:
      gain.append(vx)
    elif not sep and vx not in umad:
      loss.append(vx)
  return gain, loss


def fill_pattern(n,d,u,pattern):
  sofar = [-1]*n
  def recur(x):
    if x >= n:
      yield sofar
      return
    if pattern[x] >= 0:
      sofar[x] = pattern[x]
      yield from recur(x+1)
    else:
      g = u[x]//d
      for symbol in range(g*d, (g+1)*d):
        sofar[x] = symbol
        yield from recur(x+1)
  yield from recur(0)


def make_pots(n,d,u):



def main(n,d):
  # make the initial array
  pre = [list(range(n-d))]
  A = enweave(pre, n, d)
  for ux, u in enumerate(A):
    print(ux, u)
    # print(ux, pull_groups(u,d), pull_groups(symmetry(u,n),d))
    # print(u, problems[ux] or '')

  # count the problems
  lut = init_problems(A,n,d)
  for it_count in it.count():
    # print summary
    score = sum(map(len,lut))
    print(f'{datetime.now()} : {it_count} : {score}')
    print('Problems:')
    problems = []
    for ux,umad in enumerate(lut):
      if umad:
        print('  -',ux,umad)
        problems.append((len(umad), ux))

    if not problems:
      print('Done!')
      break

    # try to find a good change
    problems.sort(reverse=True)
    for _, ux in problems:
      # find a way to improve the array
      umad = lut[ux]
      pots = []
      for upot in make_pots(A,n,d,ux):
        gain, loss = eval_permutation(A, n, d, umad, ux, upot)
        if len(gain) > len(loss):
          pots.append([len(gain) - len(loss), upot, gain, loss])

      if not pots:
        print ('No pots')
        continue

      pots.sort(reverse=True)
      print(f'Pots: {len(pots)} Best: {pots[0][0]}')
      _, upot, gain, loss
      A[ux] = upot
      for vx in gain:
        lut[ux].discard(vx)
        lut[vx].discard(ux)
      for vx in loss:
        lut[ux].add(vx)
        lut[vx].add(ux)

    else:
      print('Stuck!')
      break




# main(8,2)
main(12,3)