import itertools as it
from copy import deepcopy
from datetime import datetime
from collections import Counter, defaultdict
import itertools as it
import random


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def enweave(A, n, k):
  ret = []
  highs = list(range(n-k, n))
  for row in A:
    for ps in it.combinations(list(range(n)), k):
      random.shuffle(highs)
      l, h = 0, 0
      new = []
      for i in range(n):
        if i in ps:
          new.append(highs[h])
          h += 1
        else:
          new.append(row[l])
          l += 1
      ret.append(new)
  return ret


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  i = 0
  while i < len(u) and (u[i]-v[i])**2 < dd:
    i += 1
  return i < len(u)


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


def init_problems(A, d, foes):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in foes[vx]:
      if ux < vx and not separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


def init_foes(A,n,d):
  lut = [set() for _ in A]
  for vx,v in enumerate(A):
    v = [e//d for e in v]
    for ux in range(vx):
      sep = False
      for dx,y in zip(A[ux],v):
        if abs(dx//d - y) > 1:
          sep = True
          break
      if not sep:
        lut[ux].add(vx)
        lut[vx].add(ux)
  return lut


def main(n, d):
  pre = load_pa(f'pa_{n-d}_choose_{d}.txt')
  print ('Loading from smaller')
  A = enweave(pre, n, n%d or d)

  foes = init_foes(A,n,d)
  lut = init_problems(A, d, foes)
  for u, vs in enumerate(lut):
    print(u, len(vs))

  return A


if __name__ == '__main__':
  from sys import argv
  try:
    n, d = int(argv[1]), int(argv[2])
  except:
    print('Requires N D')
    exit(1)

  pa = main(n, d)

