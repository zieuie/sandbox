from datetime import datetime

from multihaxell.worker import Worker


def find_it(n,d,eps):
  M = dict()
  w = Worker(n,d,eps)
  for A in w.colors:
    print(datetime.now(), len(M), len(w.colors))
    if A in M:
      print('Skipped')
      continue
    pot = w.grow_transversal(M, A)
    if pot is None:
      print('Failed')
      break
  return M


# globals
backup_interval = 60
HELP_STR = f'''
haxell.py

Creates (n choose d) permutation arrays using Haxell's algorithm
for independent transversals. A backup will be made every {backup_interval} seconds.

This program can resume from any partial permutation array stored in
a file named pa_n_d_haxell.txt. For example, pa_12_3_haxell.txt,
if it exists, will be used on startup.

Usage:
  pypy3 haxell.py N D [epsilon]

Where:
  N       - Permutation length
  D       - Chebyshev distance
  epsilon - A parameter for Haxell's algorithm (Default 0.1)
'''

if __name__ == '__main__':
  from sys import argv

  # parameters are globally used
  try:
    perm_len = int(argv[1])
    pa_distance = int(argv[2])
    eps = float(argv[3]) if len(argv) > 3 else 0.1
  except:
    print(HELP_STR)
    exit(1)

  state = find_it(perm_len, pa_distance, eps)
  if state is None:
    print('Failed')
    exit(1)

  print('Success')

