import itertools as it
from copy import deepcopy
from datetime import datetime

from lib import *


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


def hill_climb(A, n, d):
  N = len(A)
  s = init_separations(A, d)
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

        adders, subers, news = eval_permutation(A, src, dst, s, i, d)
        if len(news) + len(adders) > 2*len(subers):
          # improvement, great!
          print('tries', tries)
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

      row = apply_permutation(A[i], src, dst)
      update_diffs(A, s, i, row, adders, subers, news)


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

    with open(filename, 'w+') as f:
      for row in pa:
        f.write(' '.join(map(str, row)) + '\n')
