from datetime import datetime
from time import sleep
from random import randrange, seed, choice


from multihaxell.haxell import Haxell

import itertools as it
import ray
ray.init()


@ray.remote
class Worker:
  def __init__(self, n, d, eps):
    self.h = Haxell(n,d,eps)

  def grow_transversal(self, M, A, timestamp):
    start = datetime.now()
    ret = self.h.grow_transversal(M, A)
    return timestamp, A, datetime.now() - start, ret


def find_it(n,d,eps,num_workers=10):
  M = dict()
  colors = list(Haxell(n,d,eps).colors)
  missing_colors = list(colors)

  workers = [Worker.remote(n,d,eps) for _ in range(num_workers)]

  futures = dict()
  for c,a in zip(workers, missing_colors[-num_workers:]):
    future = c.grow_transversal.remote(M,a,0)
    futures[future] = c

  past = {0: set()}
  fail_count = 0
  for timestamp in it.count(1):
    # are we there yet?
    if not missing_colors:
      print('done!')
      break

    # scale up if possible
    cpus = int(ray.cluster_resources().get("CPU", 1))
    while cpus > len(workers):
      print('Scaling up from', len(workers), 'to', cpus)
      worker = Worker.remote(n,d,eps)
      workers.append(worker)
      a = missing_colors.pop(0)
      futures[worker.grow_transversal.remote(M, a, timestamp)] = worker

    # get the changes
    ready, _ = ray.wait(list(futures.keys()), num_returns=1)
    future = ready[0]
    worker = futures.pop(future)
    lamp, old_color, delta, pot = ray.get(future)

    # fire the next work
    a = missing_colors.pop(0)
    futures[worker.grow_transversal.remote(M, a, timestamp)] = worker

    # check history
    good = True
    if not pot:
      good = False
    else:
      s = set(pot.keys())
      for t in range(lamp, timestamp):
        p = past.get(t)
        if p is None or p & s:
          print('.', end='')
          good = False
          break
    # else:
      # print('good') #, timestamp, s)

    # commit and update history
    if good:
      M.update(pot)
      past[timestamp] = s
      print(datetime.now(), delta, len(M))
      fail_count = 0
    else:
      missing_colors.append(old_color)
      past[timestamp] = set()
      fail_count += 1
    past.pop(timestamp-100, None)

    # start backtracking if we have to
    if fail_count > 10:
      print('backtracking...')
      for _ in range(10):
        k = choice(list(M.keys()))
        # print('  -', k)
        M.pop(k)
        missing_colors.append(k)
      fail_count = 0

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

  with open(f'hresults/pa_{perm_len}_{pa_distance}.txt', 'w+') as f:
    for v in state.values():
      f.write(' '.join(map(str, v)) + '\n')
