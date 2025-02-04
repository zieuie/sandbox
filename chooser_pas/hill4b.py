
from copy import deepcopy
import random
import itertools as it
from datetime import datetime
from lib import *


def main(n, k, d):
  # Step 1 - Make a dumb PA
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
  count_disturbs = 0

  try:
    for it_count in it.count():
      w = sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == len(A)-1)
      # uncovered = [i for i,e in enumerate(s) if len(e) == len(A)-1]

      # should_print = it_count % 100 == 0
      should_print = True
      if W-w < best_score:
        should_print = True
        best_score = W-w
        best_pa = deepcopy(A)
        best_s = deepcopy(s)
        if w == W:
          return A
      elif W-w == best_score:
        best_pa = deepcopy(A)
        best_s = deepcopy(s)

      should_print and print(' '*10, datetime.now(), 'Iteration:', it_count, 'Score:', W-w, 'Best:', best_score, 'Coverage:', coverage, 'of', len(A), 'Disturbs:', count_disturbs)

      q = []
      for idx, si in enumerate(s):
        if len(si) == len(A)-1:
          continue
        score = 0
        for x in si:
          score += len(s[x])
        q.append((score, idx))
      q = sorted(q)

      for score, i in q:
        if score == len(A)-1:
          continue
        if best_full_permutation(A, H, L, s, i, d):
          print('permute full', i)
          break
        # if best_permutation(A, H[i], s, i, d):
        #   print('permute H', i)
        #   break
        # if best_permutation(A, L[i], s, i, d):
        #   print('permute L', i)
        #   break
      else:
        count_disturbs += 1
        # for i in range(len(A)):
        for _ in range(10):
          gently_disturb(A, H, L, s, d)

      #   if W-w > best_score:
      #     print('reset PA')
      #     A = deepcopy(best_pa)
      #     s = deepcopy(best_s)
      #   else:
      # for _ in range(10):
      #   if gently_disturb(A, H, L, s, d):
      #     count_disturbs += 1
      #     break
      # else:
        # print('disturb PA')
        # greatly_disturb(A, H, L, s, d)

  except KeyboardInterrupt:
    return best_pa


if __name__ == '__main__':
  from sys import argv
  n, d = int(argv[1]), int(argv[2])

  # The original PA is size m
  # filename = f'pa_{n}_choose_{d}.txt'
  filename = f'dump88.txt'
  pa = main(n, d, d)
  with open(filename, 'w+') as f:
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

