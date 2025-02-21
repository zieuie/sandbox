import itertools as it
from copy import deepcopy
from datetime import datetime

from lib import *
import numpy as np

HELP_STR = '''
Usage:
  pypy3 hill.py n d

Creates an (n,d)-PA of size (n choose d), where
each row has its d highest symbols in a different of the
(n choose d) positions that they could be arranged in.
'''


def load_pa_2(n, d):
  try:
    A = load_pa(f'pa_{n}_choose_{d}_unfinished.txt')
    print ('Loaded from unfinished file')
    return A
  except:
    pass

  A = load_pa(f'pa_{n-d}_choose_{d}_verified.txt')
  print ('Loaded from smaller file')
  return enweave(A, n, d)


def enweave(A, n, d):
  ret = []
  highs = list(range(n-d, n))
  for row in A:
    for ps in it.combinations(list(range(n)), d):
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


def yoink_columns(A, n, d):
  twists = n // d
  ret = [[] for _ in range(twists)]
  for row in A:
    buckets = [[] for _ in range(twists)]
    for i,e in enumerate(row):
      buckets[e//d].append(i)
    for r,b in zip(ret,buckets):
      r.append(b)
  return ret


def eval_permutation3(A, src, dst, s, ux, d):
  # before := those rows which were covered in the src columns before permuting
  R = A[ux]
  mask = np.any(np.abs(A[:, src] - R[src, None].T) >= d, axis=1)
  before = set(np.where(mask)[0])

  # after := those rows which are covered in the src columns after permuting
  R = np.array(apply_permutation(A[ux], src, dst))
  mask = np.any(np.abs(A[:, src] - R[src, None].T) >= d, axis=1)
  after = set(np.where(mask)[0])

  # unchanged := those rows which were covered outside of the src columns
  rows = list(after | before)
  meep = list( set(range(n)) - set(src) )
  mask = np.any(np.abs(A[rows][:, meep] - R[meep, None].T) >= d, axis=1)
  unchanged = set(np.array(rows)[mask])

  # print('Before', before)
  # print()
  # print('After',  after)
  # print()
  # print(len(before), len(after), len(before-after), len(after-before))
  # input()
  return before-unchanged, after-unchanged


def eval_permutation2(A, src, dst, s, ux, d):
  R = A[ux]
  mask = np.any(np.abs(A - R.T) >= d, axis=1)
  before = set(np.where(mask)[0])

  R = np.array(apply_permutation(A[ux], src, dst))
  mask = np.any(np.abs(A - R.T) >= d, axis=1)
  after = set(np.where(mask)[0])


def update_diffs2(A, s, i, row, before, after):
  A[i] = row
  s[i].update(after)
  for x in after:
    s[x].add(i)
  for x in before - after:
    s[i].discard(x)
    s[x].discard(i)


def hill_climb(A, n, d):
  N = len(A)
  s = init_separations(A, d)
  W = N * (N-1)
  P = yoink_columns(A, n, d)

  A = np.array(A)
  best_score = float('inf')
  best_pa = deepcopy(A)
  best_s = deepcopy(s)
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      score = W - sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == N-1)

      # should_print = it_count % 1000 == 0 or N == coverage
      should_print = True
      if score < best_score:
        best_score = score
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
      if score > best_score and it_count - last_tweak > 100000:
        A, s, last_tweak = deepcopy(best_pa), deepcopy(best_s), it_count
        last_tweak = it_count
        force = True

      for tries in it.count():
        i = random.randrange(N)
        src = random.choice(P)[i]
        dst = deepcopy(src)
        random.shuffle(dst)

        before, after = eval_permutation3(A, src, dst, s, i, d)
        # before, after = [], [] #eval_permutation3(A, src, dst, s, i, d)
        if len(after) > len(before):
          # improvement, great!
          last_tweak = it_count
          break
        elif len(after) == len(before) and tries > 1000 and not force:
          # wandering, sure!
          break
        elif tries > 100000 or force:
          # backtracking, maybe.
          last_tweak = it_count
          break

      # print('tries', tries)
      # row = apply_permutation(A[i], src, dst)
      # update_diffs2(A, s, i, row, before, after)

        # adders, subers, news = eval_permutation2(A, src, dst, s, i, d)
        # if len(news) + len(adders) > 2*len(subers):
        #   # improvement, great!
        #   last_tweak = it_count
        #   break
        # elif len(news) + len(adders) == 2*len(subers) and tries > 1000 and not force:
        #   # wandering, sure!
        #   break
        # elif tries > 100000 or force:
        #   # backtracking, maybe.
        #   last_tweak = it_count
        #   break

      # print('tries', tries)
      # row = apply_permutation(A[i], src, dst)
      # update_diffs(A, s, i, row, adders, subers, news)


  except KeyboardInterrupt:
    yield deepcopy(best_pa)
    pass


if __name__ == '__main__':
  from sys import argv
  try:
    n, d = int(argv[1]), int(argv[2])
  except:
    print (HELP_STR)
    exit(1)

  A = load_pa_2(n, d)

  for pa in hill_climb(A, n, d):
    if verify(pa, d):
      filename = f'pa_{n}_choose_{d}_verified.txt'
      print ('Verified', filename)
    else:
      filename = f'pa_{n}_choose_{d}_unfinished.txt'
      print ('Failed to verify', filename)

    # with open(filename, 'w+') as f:
    #   for row in pa:
    #     f.write(' '.join(map(str, row)) + '\n')
