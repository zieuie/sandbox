# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting
# And permuting the whole HIGH(i) instead of just transposing
# And doing the minimum effort to track the score changes!

from copy import deepcopy
import random
import itertools as it
from datetime import datetime
from lib import *


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
  last_printed_score = None
  last_tweak = 0

  try:
    for it_count in it.count():
      w = sum(len(e) for e in s)
      coverage = sum(1 for e in s if len(e) == len(A)-1)
      uncoverage = len(A) - coverage
      if uncoverage == 0:
        return A
      
      should_print = it_count % uncoverage == 0
      # should_print = True
      if W-w < best_score:
        best_score = W-w
        should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        best_pa = deepcopy(A)
      elif W-w == best_score:
        best_pa = deepcopy(A)

      if should_print:
        print(datetime.now(), 'Iteration:', it_count, 'Score:', W-w, 'Best:', best_score, 'Coverage:', coverage, 'of', len(A), 'Last tweak:', last_tweak)
        last_printed_score = best_score

      if it_count - last_tweak > 3000:
        A = deepcopy(best_pa)
        greatly_disturb(A, H, L, s, d)
        last_tweak = it_count
      else:
        if gently_disturb(A, H, L, s, d):
          last_tweak = it_count

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
