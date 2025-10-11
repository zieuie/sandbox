
# Let's just start over

import random
import itertools as it
from datetime import datetime
from collections import defaultdict
from time import time


### Utils

def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


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


### Specific

def pull_groups(p, d):
  return tuple(e // d for e in p)


def enweave(A, n, d):
  ret = []
  highs = list(range(n-d, n))
  for ps in it.combinations(list(range(n)), d):
    for row in A:
      u = infill(row, highs, ps)
      ret.append(u)
  return ret


def init_problems(A, n, d):
  s = [set() for _ in A]
  for vx, v in enumerate(A):
    for ux, u in enumerate(A):
      if ux != vx and not separated(u, v, d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, n, d, umad, ux, upot):
  gain = []
  loss = []
  for vx, v in enumerate(A):
    if ux == vx:
      continue
    sep = separated(upot, v, d)
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


def make_possibilities(n,d,u,vs):
  # takers[col][symbol] = [vxs that could be covered]
  takers = [defaultdict(set) for _ in range(n)]
  for vx, v in enumerate(vs):
    for col, (eu, ev) in enumerate(zip(u,v)):
      g = eu//d
      for symbol in range(g*d, (g+1)*d):
        if abs(symbol-ev) >= d:
          takers[col][symbol].add(vx)

  def recur(col, sofar, qwer):
    if col >= n:
      yield sofar, qwer
      return

    for sym, covs in takers[col].items():
      yield from recur(col+1, sofar+[sym], qwer | covs)
    yield from recur(col+1, sofar+[-1], qwer)

  bestcov = 0
  besties = []
  for pattern, coverage in recur(0, [], set()):
    if len(coverage) > bestcov:
      bestcov = len(coverage)
      besties = [list(pattern)]
    elif len(coverage) == bestcov:
      besties.append(list(pattern))
  
  ret = set()
  for pattern in besties:
    for pot in fill_pattern(n,d,u,pattern):
      ret.add(tuple(pot))

  return ret


def main(n,d):
  # make the initial array
  pre = load_pa(f'../results/pa_{n-d}_choose_{d}_verified.txt')
  R = len(pre)
  A = enweave(pre, n, d)
  for ux, u in enumerate(A):
    print(ux, u)

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
    action = None
    nonactions = []
    for _, ux in problems:
      # find a way to improve the array
      umad = lut[ux]
      pots = []

      vs = [e for i,e in enumerate(A[ux - ux%R : ux - ux%R + R]) if i != ux%R]
      for upot in make_possibilities(n,d,u,vs):
        gain, loss = eval_permutation(A, n, d, umad, ux, upot)
        if len(gain) > len(loss):
          pots.append([len(gain) - len(loss), upot, gain, loss])
        elif len(gain) == len(loss):
          nonactions.append([len(gain) - len(loss), upot, gain, loss])

      if not pots:
        print ('.', end='')
        continue

      pots.sort(reverse=True)
      if pots:
        action = pots[0]
        if action[0] > 0:
          break

    if nonactions and not action:
      action = random.choice(nonactions)

    if not action:
      print('Stuck!')
      break

    _, upot, gain, loss
    A[ux] = upot
    for vx in gain:
      lut[ux].discard(vx)
      lut[vx].discard(ux)
    for vx in loss:
      lut[ux].add(vx)
      lut[vx].add(ux)





# main(8,2)
# main(12,3)
main(8,3)