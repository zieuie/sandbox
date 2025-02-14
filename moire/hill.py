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
      uncoverage = len(A) - coverage
      if uncoverage == 0:
        print ('Fully covered')

        return A
      
      # should_print = it_count % uncoverage == 0
      should_print = it_count % 10000 == 0
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

      if W-w > best_score and it_count - last_tweak > 1000000:
        A = deepcopy(best_pa)
        s = deepcopy(best_s)
        greatly_disturb(A, H, L, s, d)
        last_tweak = it_count
      elif W-w == best_score and it_count - last_tweak > 100000:
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
