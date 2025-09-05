import itertools as it
from copy import deepcopy
from datetime import datetime
from collections import Counter
import itertools as it
import random
import math


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
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


def init_problems(A, d, foes):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in foes[vx]:
      if ux < vx and not separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, ux, d, upot, lut, foes):
  gain = []
  loss = []
  umad = lut[ux]
  for vx in foes[ux]:
    if ux == vx:
      continue
    v = A[vx]
    sep = separated(upot, v, d)
    if sep and vx in umad:
      gain.append(vx)
    elif not sep and vx not in umad:
      loss.append(vx)
  return gain, loss


def update_diffs(A, lut, i, row, gain, loss, lutmap):
  A[i] = row

  lutmap[len(lut[i])].discard(i)
  lutmap[len(lut[i]) - len(gain) + len(loss)].add(i)

  for x in gain:
    lutmap[len(lut[x])].discard(x)
    lutmap[len(lut[x])-1].add(x)
    lut[i].discard(x)
    lut[x].discard(i)
  for x in loss:
    lutmap[len(lut[x])].discard(x)
    lutmap[len(lut[x])+1].add(x)
    lut[i].add(x)
    lut[x].add(i)


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


from collections import Counter, defaultdict
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


def all_pots(n,d,u):
  one = [pull_group(u,n,d,x) for x in range(n//d + int(bool(n%d)))]
  one_chained = list(it.chain(*one))
  for x, two in enumerate(it.product(*map(it.permutations, one))):
    two_chained = list(it.chain(*two))
    if one_chained == two_chained:
      continue
    yield one_chained, two_chained


def thoroughly_disturb(A,n,d,lut,foes,lutmap):
  used = set()
  def recur(depth=0):
    goods = []
    for i in range(len(A)):
      if not lut[i] or i in used:
        continue
      for one, two in all_pots(n,d, A[i]):
        row = apply_permutation(A[i], one, two)
        gain, loss = eval_permutation(A, i, d, row, lut, foes)
        diff = len(gain)-len(loss)
        if diff >= 0 and (gain or loss):
          goods.append((diff, i, row, gain, loss))
    
    goods.sort(reverse=True)
    # random.shuffle(goods)
    print(f'depth {depth} best {goods[0]} len {len(goods)} used {used}')
    if depth >= 2:
      for t in goods:
        if t[0] > 0:
          yield t[1:]
      return

    for diff, i, row, gain, loss in goods:
      bkp = A[i]
      A[i] = row
      update_diffs(A, lut, i, row, gain, loss, lutmap)
      used.add(i)
      yield from recur(depth+1)
      used.discard(i)
      update_diffs(A, lut, i, row, loss, gain, lutmap)
      A[i] = bkp

  for t in recur():
    print('t', t)
    yield t

  # print('Goods', goods)
  # print(f'Thoroughly found {len(goods)}')
  # if not goods:
  #   return (None,)*4
  # return goods[0][1:]



if __name__ == '__main__':
  n,d = 8,3
  A = load_pa(f'pa_{n}_choose_{d}.txt')
  foes = init_foes(A,n,d)
  lut = init_problems(A, d, foes)
  lutmap = defaultdict(set)
  for k,v in enumerate(foes):
    lutmap[len(v)].add(k)
  i, row, gain, loss = thoroughly_disturb(A,n,d,lut,foes,lutmap)


# for x in enumerate(all_pots(8,3,[2,1,4,3,6,5,7,0])):
#   print(x)