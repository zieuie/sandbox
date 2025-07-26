
import itertools as it
import json
import random
from copy import deepcopy
from datetime import datetime

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


# def complement(p, n):
#   return [(e+n//2) % n for e in p]


# def pull_groups(p, d):
#   return tuple(e // d for e in p)


def resume_calculation(n, d):
  try:
    A = load_pa(f'pa_{n}_choose_{d}_lazy_bump_half_unfinished.txt')
    with open(f's_{n}_choose_{d}_lazy_bump_half_unfinished.txt', 'r') as f:
      s = list(map(set, json.load(f)))
    print ('Loaded from unfinished file')
    return A, s
  except Exception as e:
    print(e)
    pass

  try:
    if n-d == d:
      A = [list(range(n-d))]
      print ('Randomly generating')
    else:
      A = load_pa(f'pa_{n-d}_choose_{d}_verified.txt')
      print ('Loaded from smaller file')
    ret = enweave(A, n, d)
    return ret, None
  except FileNotFoundError:
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
      ret.append(u)

      # v = complement(u, n)
      # gu = pull_groups(u, d)
      # gv = pull_groups(v, d)
      # if gu not in seen and gv not in seen:
      #   seen.add(gu)
      #   seen.add(gv)
      #   ret.append(u)

  return ret


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
    if separated(pot, e, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news


def preval_separations(A, d, s, vx):
  v = A[vx]
  for ux, u in enumerate(A):
    if ux != vx and separated(u, v, d):
      s[ux].add(vx)
      s[vx].add(ux)
  return s


def hill_climb(A, n, d, s=None):
  N = len(A)
  s = s or [set() for _ in range(N)]
  updated = [False]*N
  W = N * (N-1)

  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_printed_score = float('inf')
  last_printed_it = -1
  last_tweak = 0

  try:
    for it_count in it.count():
      score = W - sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == N-1)

      should_print = it_count % 100 == 0
      if score < best_score:
        best_score = score
        should_print = True
        best_pa = deepcopy(A)
        best_s = deepcopy(s)
        # if last_printed_score - best_score > 100 or (it_count - last_printed_it > 1000 and last_printed_score > best_score): 
        #   last_printed_score = best_score
        #   last_printed_it = it_count
        #   yield deepcopy(best_pa), list(map(list, best_s))

      if should_print:
        print(datetime.now(), f'P({n},{d})', 'Iteration:', it_count, 'Score:', score, 'Best:', best_score, 'Coverage:', coverage, 'of', N, 'Last tweak:', last_tweak)

      if N == coverage:
        yield deepcopy(A), None
        return

      force = False
      if score >= best_score and it_count - last_tweak > 10000:
        A, s, last_tweak, force = deepcopy(best_pa), deepcopy(best_s), it_count, True

      i = random.randrange(N)
      if not updated[i]:
        preval_separations(A, d, s, i)
        updated[i] = True

      for tries in it.count():
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
    yield deepcopy(best_pa), list(map(list, best_s))
    pass


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


if __name__ == '__main__':
  from sys import argv
  n = int(argv[1])
  d = int(argv[2]) if len(argv) > 2 else n//4

  # if len(argv) >= 3 and int(argv[2]) != d:
  #   print ('This program only works when d = n/4')
  #   exit(1)

  A,s = resume_calculation(n, d)
  for pot,pots in hill_climb(A, n, d, s):
    pa = list(pot)
    # for row in pot:
    #   pa.append(complement(row, n))

    if verify(pa, d):
      filename = f'pa_{n}_choose_{d}_verified.txt'
      print ('Verified', filename)
      with open(filename, 'w+') as f:
        for row in pa:
          f.write(' '.join(map(str, row)) + '\n')
    else:
      filename = f'pa_{n}_choose_{d}_lazy_bump_half_unfinished.txt'
      print ('Failed to verify', filename)
      with open(filename, 'w+') as f:
        for row in pot:
          f.write(' '.join(map(str, row)) + '\n')

      filename = f's_{n}_choose_{d}_lazy_bump_half_unfinished.txt'
      with open(filename, 'w+') as f:
        json.dump(pots, f)

