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


def symmetric_separated(u,v,n,d):
  cu = complement(u, n)
  cv = complement(v, n)
  return separated(u,v,d) and separated(cu,v,d) and separated(u,cv,d) and separated(cu,cv,d)


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


def load_pa_2(n, d):
  try:
    A = load_pa(f'pa_{n}_choose_{d}_symmetric_unfinished.txt')
    print ('Loaded from unfinished file')
    return A
  except:
    pass

  print ('Randomly generating')
  A = [list(range(n-d))]
  ret = enweave(A, n, d)
  return ret


def complementary_combinations(n, d):
  seen = set()
  for ps in it.combinations(list(range(n)), d):
    comp = tuple(x for x in range(n) if x not in ps)
    if ps in seen or comp in seen:
      continue
    seen.add(ps)
    yield ps


def enweave(A, n, d):
  ret = []
  highs = list(range(n-d, n))
  for row in A:
    for ps in complementary_combinations(n, d):
      while True:
        random.shuffle(highs)
        new = infill(row, highs, ps)
        if separated(new, complement(new, n), d):
          ret.append(new)
          break

  return ret


def yoink_columns(A, n, d):
  twists = n // d
  ret = [[] for _ in range(twists)]
  for row in A:
    buckets = [[] for _ in range(twists)]
    for i,e in enumerate(row):
      # print (i, e, n, d, e//d, twists)
      buckets[e//d].append(i)
    for r,b in zip(ret,buckets):
      r.append(b)
  return ret


def init_separations(A, n, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and symmetric_separated(A[ux], A[vx], n, d):
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
    if symmetric_separated(pot, e, n, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news




def hill_climb(A, n, d):
  N = len(A)
  s = init_separations(A, n, d)
  W = N * (N-1)
  P = yoink_columns(A, n, d)

  # A = np.array(A)
  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_printed_score = float('inf')
  last_tweak = 0

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

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', score, 'Best:', best_score, 'Coverage:', coverage, 'of', N, 'Last tweak:', last_tweak)
        last_printed_score = best_score

      if N == coverage:
        yield deepcopy(A)
        return

      force = False
      if score >= best_score and it_count - last_tweak > 10000:
        A, s, last_tweak, force = deepcopy(best_pa), deepcopy(best_s), it_count, True

      for tries in it.count():
        i = random.randrange(N)
        src = random.choice(P)[i]
        dst = deepcopy(src)
        random.shuffle(dst)
        row = apply_permutation(A[i], src, dst)

        if not separated(row, complement(row, n), d):
          # print('not separated')
          continue

        adders, subers, news = eval_permutation(A, n, row, s, i, d)
        if len(news) + len(adders) > 2*len(subers):
          # improvement, great!
          # print('tries', tries)
          last_tweak = it_count
          break
        elif len(news) + len(adders) == 2*len(subers) and tries > 100 and not force:
          # wandering, sure!
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


def complement(p, n):
  return [n-1-e for e in p]


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


if __name__ == '__main__':
  from sys import argv
  n = int(argv[1])
  d = n//2

  if len(argv) >= 3 and int(argv[2]) != d:
    print ('This program only works when d = n/2')
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
      filename = f'pa_{n}_choose_{d}_symmetric_unfinished.txt'
      print ('Failed to verify', filename)
      with open(filename, 'w+') as f:
        for row in pot:
          f.write(' '.join(map(str, row)) + '\n')

