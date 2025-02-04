# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting
# And permuting the whole HIGH(i) instead of just transposing
# And doing the minimum effort to track the score changes!

from copy import deepcopy
import random
import itertools as it
from datetime import datetime
from lib import *


# Find the best permutation of A[i]
def best_permutation(A, start, s, i, d):
  bestw = 0
  end = None

  for x, target in enumerate(it.permutations(start)):
    pot = apply_permutation(A[i], start, target)

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += len(s[x])
        if i not in s[x]:
          w += 1

    if w > bestw:
      bestw, end = w, target

  return bestw, start, end


# Find the best permutation of A[i]
def good_permutation(A, start, s, i, d, beat):
  target = [e for e in start]
  for _ in range(100):
    random.shuffle(target)
    pot = apply_permutation(A[i], start, target)

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += len(s[x])
        # if i not in s[x]:
        #   w += 1
    if w > beat:
      return w, start, target

  return 0, None, None


def kid_permutation(A, start, s, i, d):
  target = [e for e in start]
  random.shuffle(target)
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
  return start, target, adders, subers, news



def disturb_gently(A, H, L, s):
  while True:
    i = random.randrange(len(A))
    one, two, adders, subers, news = kid_permutation(A, random.choice((H, L))[i], s, i, d)
    if len(news) + len(adders) >= 2*len(subers):
      # A[i] = apply_permutation(A[i], one, two)
      return one, two, adders, subers, news


def disturb(A, H, L, s):
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  # for _ in range(random.randrange(1, 4)):
  for _ in range(1):
    i = random.randrange(len(q))
    i = q[i][1]

    ret = [e for e in A[i]]

    ps = [e for e in H[i]]
    qs = [e for e in H[i]]
    random.shuffle(qs)
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]

    ps = [e for e in L[i]]
    qs = [e for e in L[i]]
    random.shuffle(qs)
    for u,v in zip(ps,qs):
      ret[u] = A[i][v]

  A[i] = ret


def init_separations(A, d):
  # Compute the separations of each row
  s = [[] for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].append(vx)
        s[vx].append(ux)

  # compute the score of each row
  scores = []
  for si in s:
    # if len(si) == len(A)-1:
    #   continue
    score = 0
    for x in si:
      score += len(s[x])
    scores.append(score)

  return s, scores


def main(n, k, d):
  # Step 1 - Make a dumb PA
  A, H, L = dumb_pa(n, k)
  try:
    A = load_pa(filename)
  except FileNotFoundError:
    pass

  best_coverage = 0
  best_score = float('inf')
  best_pa = deepcopy(A)

  count_disturbs = 0
  counter = Counter()
  q_idx = 0

  try:
    for it_count in it.count():
      should_print = True

      s, scores = init_separations(A, d)
      w = sum(len(A)-1-len(e) for e in s)
      if w <= best_score:
        should_print = True
        best_score = w
        best_coverage = sum(1 for row in s if len(row) == len(A)-1)
        best_pa = deepcopy(A)
        if 0 == best_coverage:
          return A

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', w, 'Best score:', best_score, 'Best coverage:', len(A) - best_coverage, 'of', len(A), 'Disturbances:', count_disturbs, counter)

      # Step 4 - Find the least separated row...
      q = sorted((s,idx) for idx,s in enumerate(scores))
      for zxcv in range(min(10, len(q))):
      # for zxcv in range(len(q)):
        si, i = q[(zxcv+q_idx) % len(q)]
        # Step 5 - ...and find the best improved transposition
        separations, one, two = good_permutation(A, H[i], s, i, d, si)  # Try the highs
        if separations <= si:
          separations, one, two = good_permutation(A, L[i], s, i, d, si)  # Try the lows
        # if separations <= si and zxcv < 10:
        #   separations, one, two = best_permutation(A, H[i], s, i, d)  # Try the highs
        # if separations <= si and zxcv < 10:
        #   separations, one, two = best_permutation(A, L[i], s, i, d)  # Try the lows

        # We found a good transposition
        if separations > si:
          A[i] = apply_permutation(A[i], one, two)
          counter.update([zxcv])
          # q_idx = (zxcv+q_idx) % len(q)
          break
      else:
        # if w >= best_score + 10:
        if w >= best_score:
          A = deepcopy(best_pa)
          count_disturbs = 0
        
        if count_disturbs % 10 == 0:
          disturb(A, H, L, s)
          count_disturbs += 10**8+1
        else:
          disturb_gently(A, H, L, s, scores)
          count_disturbs += 1
  except KeyboardInterrupt:
    pass

  return best_pa


if __name__ == '__main__':
  from sys import argv
  n, d = int(argv[1]), int(argv[2])

  # The original PA is size m
  # filename = f'pa_{n}_choose_{d}.txt'
  filename = f'dump88.txt'
  pa = main(n, d, d)
  with open(filename, 'w+') as f:
    disagreements = disagreement_counter(pa, d)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

