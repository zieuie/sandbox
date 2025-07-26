
'''
Consider 12,3.

Can't we make a mapping that takes a permutation, and
where we excahnge groups?

e.g.,

01 02 03 04 05 06 07 08 09 10 11 12

07 08 09 10 11 12 01 02 03 04 05 06


ABCD
CDAB

'''

import itertools as it
from copy import deepcopy
from datetime import datetime
import random

############################### Utils

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
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def infill(row, highs, ps):
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


############################### Special Utils


def complement(p, n):
  return [(e+n//2) % n for e in p]

def pull_groups(p, d):
  return tuple(e // d for e in p)


def load_pa_2(n, d):
  try:
    A = load_pa(f'pa_{n}_choose_{d}_bump_half_unfinished.txt')
    print ('Loaded from unfinished file')
    return A
  except:
    pass

  try:
    A = load_pa(f'pa_{n-d}_choose_{d}_verified.txt')
    ret = enweave(A, n, d)
    print ('Loaded from smaller file')
    return ret
  except:
    print (f'No smaller file exists. Must create pa_{n-d}_choose_{d}_verified.txt')
    exit(1)
    pass



def enweave(A, n, d):
  ret = []
  seen = set()
  highs = list(range(n-d, n))
  for row in A:
    for ps in it.combinations(list(range(n)), d):
      random.shuffle(highs)
      u = infill(row, highs, ps)
      v = complement(u, n)

      gu = pull_groups(u, d)
      gv = pull_groups(v, d)

      # print(u, v)
      # print(gu, gv)


      if gu not in seen and gv not in seen:
        seen.add(gu)
        seen.add(gv)
        ret.append(u)

  return ret


# def yoink_row(row, n, d):
#   buckets = [[] for _ in range(n // d)]
#   for i,e in enumerate(row):
#     buckets[e//d].append(i)
#   yield buckets


# def yoink_columns(A, n, d):
#   twists = n // d
#   ret = [[] for _ in range(twists)]
#   for row in A:
#     buckets = [[] for _ in range(twists)]
#     for i,e in enumerate(row):
#       # print (i, e, n, d, e//d, twists)
#       buckets[e//d].append(i)
#     for r,b in zip(ret,buckets):
#       r.append(b)
#   return ret


def init_separations(A, n, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    if vx % 1000 == 0:
      print(vx, len(A))
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], n):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def update_diffs(A, s, i, row, adders, subers, news):
  A[i] = row
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)


def eval_permutation(A, n, pot, s, i, d):
  news = []
  adders = []
  subers = []
  for x, e in enumerate(A):
    if x == i:
      continue
    if separated(pot, e, n):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news




def hill_climb(A, n, d):
  N = len(A)
  print('a', len(A))
  s = init_separations(A, n, d)
  print('b')
  W = N * (N-1)
  # P = yoink_columns(A, n, d)
  print('c')

  # A = np.array(A)
  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_printed_score = float('inf')
  last_printed_it = -1
  last_tweak = 0
  print('d')

  try:
    for it_count in it.count():
      score = W - sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == N-1)

      should_print = it_count % 100 == 0
      # should_print = True
      if score < best_score:
        best_score = score
        should_print = True
        # should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        best_pa = deepcopy(A)
        best_s = deepcopy(s)
        if last_printed_score - best_score > 100 or (it_count - last_printed_it > 1000 and last_printed_score > best_score): 
          last_printed_score = best_score
          last_printed_it = it_count
          yield deepcopy(best_pa)

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', score, 'Best:', best_score, 'Coverage:', coverage, 'of', N, 'Last tweak:', last_tweak)

      if N == coverage:
        yield deepcopy(A)
        return

      force = False
      if score >= best_score and it_count - last_tweak > 10000:
        A, s, last_tweak, force = deepcopy(best_pa), deepcopy(best_s), it_count, True

      for tries in it.count():
        i = random.randrange(N)
        # src = random.choice(P)[i]
        # src = random.choice(yoink_row(A[i], n, d))
        k = random.randrange(n//d)
        src = [x for x,e in enumerate(A[i]) if e//d == k]
        dst = deepcopy(src)
        random.shuffle(dst)
        row = apply_permutation(A[i], src, dst)

        # if not separated(row, complement(row, n), d):
        #   # print('not separated')
        #   continue

        adders, subers, news = eval_permutation(A, n, row, s, i, d)
        if len(news) + len(adders) > 2*len(subers):
          # improvement, great!
          # print('improve', tries)
          last_tweak = it_count
          break
        elif len(news) + len(adders) == 2*len(subers) and tries > 100 and not force:
          # wandering, sure!
          # print('wander ', tries)
          break
        elif tries > 100000 or force:
          # backtracking, maybe.
          print ('backtracking...')
          last_tweak = it_count
          break

      # print('update')
      # input()
      update_diffs(A, s, i, row, adders, subers, news)


  except KeyboardInterrupt:
    yield deepcopy(best_pa)
    pass


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


if __name__ == '__main__':
  from sys import argv
  # n = int(argv[1])
  n = 12
  d = n//4

  if len(argv) >= 3 and int(argv[2]) != d:
    print ('This program only works when d = n/4')
    exit(1)

  A = load_pa_2(n, d)
  for pot in hill_climb(A, n, d):
    pa = list(pot)
    for row in pot:
      pa.append(complement(row, n))

    if verify(pa, d):
      filename = f'pa_{n}_choose_{d}_verified.txt'
      print ('Verified', filename)
      with open(filename, 'w+') as f:
        for row in pa:
          f.write(' '.join(map(str, row)) + '\n')
    else:
      filename = f'pa_{n}_choose_{d}_bump_half_unfinished.txt'
      print ('Failed to verify', filename)
      with open(filename, 'w+') as f:
        for row in pot:
          f.write(' '.join(map(str, row)) + '\n')

