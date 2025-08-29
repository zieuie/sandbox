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


def init_separations(A, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, start, target, s, i, d):
  pot = apply_permutation(A[i], start, target)
  news = []
  adders = []
  subers = []
  for x, e in enumerate(A):
    if x == i:
      continue
    if separated(pot, e, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news


def update_diffs(A, s, i, row, adders, subers, news):
  A[i] = row
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)


# Mutates A, s
def gently_disturb(A, H, L, s, d, givens=None):
  while True:
    i = random.randrange(len(A)) if givens is None else random.choice(givens)
    one = random.choice((H, L))[i]
    # one = H[i]
    two = [e for e in one]
    random.shuffle(two)
    adders, subers, news = eval_permutation(A, one, two, s, i, d)
    if len(news) + len(adders) >= 2*len(subers):
      break

  row = apply_permutation(A[i], one, two)
  update_diffs(A, s, i, row, adders, subers, news)  
  return len(news) + len(adders) > 2*len(subers)


def greatly_disturb(A, H, L, s, d):
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  # i = random.randrange(10)
  i = q[-1][1]

  hps = [e for e in H[i]]
  random.shuffle(hps)

  lps = [e for e in L[i]]
  random.shuffle(lps)

  one = list(H[i]) + list(L[i])
  two = hps + lps
  
  # one = list(H[i])
  # two = hps

  adders, subers, news = eval_permutation(A, one, two, s, i, d)

  row = apply_permutation(A[i], one, two)
  update_diffs(A, s, i, row, adders, subers, news)


def main(n, k, d):
  A, H, L = dumb_pa(n, k)
  try:
    A = load_pa(filename)
  except FileNotFoundError:
    pass

  s = init_separations(A, d)
  W = len(A) * (len(A)-1)

  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      w = sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == len(A)-1)

      # should_print = it_count % uncoverage == 0
      should_print = it_count % 10000 == 0 or len(A) == coverage
      # should_print = True
      # should_print = False
      if W-w < best_score:
        best_score = W-w
        should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        best_pa = deepcopy(A)
        best_s = deepcopy(s)
      elif W-w == best_score and random.random() < 2:
        best_pa = deepcopy(A)
        best_s = deepcopy(s)

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', W-w, 'Best:', best_score, 'Coverage:', coverage, 'of', len(A), 'Last tweak:', last_tweak)
        last_printed_score = best_score

      if len(A) == coverage:
        return A

      if W-w > best_score and it_count - last_tweak > 100000:
        A = deepcopy(best_pa)
        s = deepcopy(best_s)
        greatly_disturb(A, H, L, s, d)
        last_tweak = it_count
      elif W-w == best_score and it_count - last_tweak > 1000:
        greatly_disturb(A, H, L, s, d)
        last_tweak = it_count
      elif gently_disturb(A, H, L, s, d):
        last_tweak = it_count

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
