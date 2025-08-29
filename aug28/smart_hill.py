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
  A = []
  for ps in it.combinations(list(range(n)), k):
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
  return A


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


def update_diffs(A, lut, i, row, gain, loss):
  A[i] = row
  for x in gain:
    lut[i].discard(x)
    lut[x].discard(i)
  for x in loss:
    lut[i].add(x)
    lut[x].add(i)


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


def meep(foes):
  # oh man i love this part
  mapping = defaultdict(set)
  for k,v in enumerate(foes):
    mapping[len(v)].add(k)

  pop = list(mapping.keys())
  weights = [(e+1)**2 for e in pop]

  size = random.choices(pop, weights=weights, k=1)[0]
  return random.choice(list(mapping[size]))


def gently_disturb(A,n,d, lut, foes):
  while True:
    i = meep(lut)
    one = pull_group(A[i],n,d,random.randrange(n//d))
    two = [e for e in one]
    random.shuffle(two)
    row = apply_permutation(A[i], one, two)
    gain, loss = eval_permutation(A, i, d, row, lut, foes)
    if len(gain) >= len(loss):
      return i, row, gain, loss


def greatly_disturb(A,n,d,lut,foes):
  i = meep(lut)
  one, two = [], []
  for x in range(n//d):
    src = pull_group(A[i], n, d, x)
    dst = [e for e in src]
    random.shuffle(dst)
    one.extend(src)
    two.extend(dst)

  row = apply_permutation(A[i], one, two)
  gain, loss = eval_permutation(A, i, d, row, lut, foes)
  return i, row, gain, loss


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


def main(n, k, d):
  try:
    A = load_pa(filename)
  except FileNotFoundError:
    A = dumb_pa(n, k)

  foes = init_foes(A,n,d)
  lut = init_problems(A, d, foes)

  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(lut)
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      w = sum(map(len, lut))
      if not w:
        print('Done!')
        return A

      coverage = sum(1 for e in lut if len(e) == 0)
      should_print = it_count % 10000 == 0 or len(A) == coverage
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

      if w > best_score and it_count - last_tweak > 100000:
        A = deepcopy(best_pa)
        lut = deepcopy(best_s)
        i, row, gain, loss = greatly_disturb(A,n,d,lut,foes)
        last_tweak = it_count
      elif w == best_score and it_count - last_tweak > 1000:
        i, row, gain, loss = greatly_disturb(A,n,d,lut,foes)
        last_tweak = it_count
      else:
        i, row, gain, loss = gently_disturb(A,n,d,lut,foes)
        if len(gain) > len(loss):
          last_tweak = it_count
      update_diffs(A, lut, i, row, gain, loss)

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
