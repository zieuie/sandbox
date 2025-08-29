import itertools as it
from copy import deepcopy
from datetime import datetime
from collections import Counter
import itertools as it
import random

from lib import *


HELP_STR = '''
Usage:
  pypy3 hill.py n d

Creates an (n,d)-PA of size (n choose d), where
each row has its d highest symbols in a different of the
(n choose d) positions that they could be arranged in.
'''



def dumb_pa(n, k):
  m = n-k
  sr = set(range(n))
  A = []
  H = list(it.combinations(list(range(n)), k))
  L = [sorted(sr-set(e)) for e in H]
  for ps in H:
    lows = tuple(range(m))
    highs = tuple(range(m, n))
    h, l = 0, 0
    row = []
    for i in range(n):
      if i in ps:
        row.append(highs[h])
        h += 1
      else:
        row.append(lows[l])
        l += 1
    A.append(row)
  return A, H, L


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True
       

def disagreement_counter(pa, d):
  ret = []
  c = Counter()
  for vx, v in enumerate(pa):
    for ux in range(vx):
      u = pa[ux]
      separated = False
      for a,b in zip(u,v):
        if abs(a-b) >= d:
          separated = True
          break
      if not separated:
        ret.append((ux,vx))
        c.update([ux])
        c.update([vx])
  return c


# def init_separations(A, d, foes):
def init_problems(A, d, foes):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and not separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, start, target, lut, i, d, foes):
  pot = apply_permutation(A[i], start, target)
  news = []
  adders = []
  subers = []
  for x in foes[i]:
  # for x in range(len(A)):
    e = A[x]
    if x == i:
      continue
    if separated(pot, e, d):
      if x in lut[i]:
        news.append(x)
      if i in lut[x]:
        adders.append(x)
    elif x not in lut[i]:
      subers.append(x)
  return adders, subers, news


def update_diffs(A, lut, i, row, adders, subers, news):
  A[i] = row
  for x in news:
    lut[i].discard(x)
  for x in adders:
    lut[x].discard(i)
  for x in subers:
    lut[i].add(x)
    lut[x].add(i)


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


# Mutates A, s
def gently_disturb(A,n,d, lut, foes):
  while True:
    i = random.randrange(len(A))
    one = pull_group(A[i],n,d,random.randrange(n//d))
    two = [e for e in one]
    random.shuffle(two)
    adders, subers, news = eval_permutation(A, one, two, lut, i, d, foes)
    if len(news) + len(adders) >= 2*len(subers):
      break

  row = apply_permutation(A[i], one, two)
  update_diffs(A, lut, i, row, adders, subers, news)  
  return len(news) + len(adders) > 2*len(subers)


def greatly_disturb(A,n,d,lut,foes):
  q = sorted([(len(si), idx) for idx,si in enumerate(lut)])
  # i = random.randrange(10)
  i = q[0][1]

  one, two = [], []
  for x in range(n//d):
    src = pull_group(A[i], n, d, x)
    dst = [e for e in src]
    random.shuffle(dst)
    one.extend(src)
    two.extend(dst)

  adders, subers, news = eval_permutation(A, one, two, lut, i, d, foes)

  row = apply_permutation(A[i], one, two)
  update_diffs(A, lut, i, row, adders, subers, news)


from collections import Counter, defaultdict
def init_foes(A,n,d):
  lut = defaultdict(set)
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

# def init_foes(A,n,d):
#   lut = defaultdict(set)
#   for vx,v in enumerate(A):
#     v = [e//d for e in v]
#     for ux in range(vx):
#       u = [e//d for e in A[ux]]
#       if max(abs(x-y) for x,y in zip(u,v)) < 2:
#         lut[ux].add(vx)
#         lut[vx].add(ux)
#   return lut



def main(n, k, d):
  A, _, _ = dumb_pa(n, k)
  try:
    A = load_pa(filename)
  except FileNotFoundError:
    pass

  foes = init_foes(A,n,d)
  # for ux, vxs in foes.items():
  #   for vx in vxs:
  #     print ('---')
  #     print ([e//d for e in A[ux]])
  #     print ([e//d for e in A[vx]])
  # return
  # s = init_separations(A, d, foes)
  lut = init_problems(A, d, foes)

  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(lut)
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      w = sum(map(len, lut))
      coverage = sum(1 for e in lut if len(e) == 0)

      # should_print = it_count % uncoverage == 0
      should_print = it_count % 10000 == 0 or len(A) == coverage
      # should_print = True
      # should_print = False
      if w < best_score:
        best_score = w
        should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        best_pa = deepcopy(A)
        best_s = deepcopy(lut)
      elif w == best_score and random.random() < 2:
        best_pa = deepcopy(A)
        best_s = deepcopy(lut)

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', w, 'Best:', best_score, 'Coverage:', coverage, 'of', len(A), 'Last tweak:', last_tweak)
        last_printed_score = best_score

      if len(A) == coverage:
        return A


      if w > best_score and it_count - last_tweak > 100000:
        A = deepcopy(best_pa)
        lut = deepcopy(best_s)
        greatly_disturb(A,n,d,lut,foes)
        last_tweak = it_count
      elif w == best_score and it_count - last_tweak > 1000:
        greatly_disturb(A,n,d,lut,foes)
        last_tweak = it_count
      elif gently_disturb(A,n,d,lut,foes):
        last_tweak = it_count


      # gently_disturb(A, H, L, lut, d, foes)
      # last_tweak = it_count

  except KeyboardInterrupt:
    pass

  return best_pa


if __name__ == '__main__':
  from sys import argv
  try:
    n, d = int(argv[1]), int(argv[2])
  except:
    print (HELP_STR)
    exit(1)

  # The original PA is size m
  filename = f'pa_{n}_choose_{d}.txt'
  pa = main(n, d, d)
  if verify(pa, d):
    print ('Verified')
  else:
    print ('Failed to verify')

  with open(filename, 'w+') as f:
    disagreements = disagreement_counter(pa, d)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')
