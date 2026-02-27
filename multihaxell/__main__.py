from datetime import datetime
from time import sleep
from random import randrange, seed, choice


from multihaxell.haxell import Haxell, make_colors

import os
import itertools as it
import ray
ray.init(
    address="auto",
    runtime_env={
        "working_dir": ".",   # ship the current directory (repo) to workers
        # optional if you also need pip deps installed on workers:
        # "pip": ["numpy==1.26.4", "networkx==3.2.1"],
    },
)

@ray.remote(num_cpus=1)
class Worker:
  def __init__(self, n, d, colors, eps):
    self.h = Haxell(n,d,colors,eps)

  def grow_transversal(self, M, A, timestamp):
    start = datetime.now()
    ret = self.h.grow_transversal(M, A)
    return timestamp, A, datetime.now() - start, ret


def good_history(M, fchanges, timestamp_to_diff, start, end, dsquared):
  if not fchanges:
    return False

  for t in range(start, end):
    for k in timestamp_to_diff.get(t, tuple()):
      if k in fchanges:
        continue
      for u in fchanges.values():
        for x,y in zip(u,M[k]):
          if (x-y)**2 < dsquared:
            return False

  return True


def find_it(n,d,eps,M=None):
  M = M or dict()
  colors = make_colors(n,d)
  dsquared = d*d

  # tracking workers and tasks in flight
  free_workers = list()
  worker_to_color = dict()  # worker to color
  future_to_worker = dict()  # future to worker
  color_to_workers = dict()  # colors being worked on

  # tracking history and things received
  remaining_colors = list(set(colors) - set(M.keys()))
  timestamps_received = set()
  timestamp_to_diff = dict()
  timestamps_in_flight = []
  latest_cutoff = 0

  backup_file = f'partial_pa_{n}_{d}.txt.tmp'
  real_file = f'partial_pa_{n}_{d}.txt'

  # tracking progress
  red_count = 0
  fail_count = 0
  # loop forever
  for timestamp in it.count(1):
    # are we there yet?
    if len(M) == len(colors):
      print('done!')
      break

    try:
      # make more workers if necessary
      cpus = int(ray.cluster_resources().get("CPU", 1))
      while len(worker_to_color) + len(free_workers) < cpus:
        print(f'Increasing workers from {len(worker_to_color) + len(free_workers)} to {cpus}')
        free_workers.append(Worker.remote(n,d,colors=colors,eps=eps))

      # assign workers
      while free_workers:
        a = choice(remaining_colors)
        # while color_to_workers.get(a, None): # and len(free_workers) < len(remaining_colors):
        #   a = choice(remaining_colors)
        w = free_workers.pop(0)
        f = w.grow_transversal.remote(M,a,timestamp)
        timestamps_in_flight.append(timestamp)
        worker_to_color[w] = a
        future_to_worker[f] = w
        color_to_workers.setdefault(a, list()).append(w)

      # get a change
      ready, _ = ray.wait(list(future_to_worker.keys()), num_returns=1)
      if not ready:
        print('not ready')
        continue
      future = ready[0]
      worker = future_to_worker.pop(future)
      ftime, fcolor, fduration, fchanges = ray.get(future)
      timestamps_received.add(ftime)

      free_workers.append(worker)
      worker_to_color.pop(worker, None)
      color_to_workers[fcolor].remove(worker)
      timestamps_in_flight.remove(ftime)

      # check if history is compatible
      if fcolor not in M and good_history(M, fchanges, timestamp_to_diff, ftime, timestamp, dsquared):
        # fix trackers
        remaining_colors.remove(fcolor)
        timestamp_to_diff.setdefault(timestamp, set()).update(fchanges.keys())
        M.update(fchanges)

        # write backup
        with open(backup_file, 'w+') as f:
          for v in M.values():
            f.write(' '.join(map(str, v)) + '\n')

        if 0 != os.system(f'cp -f {backup_file} {real_file}'):
          print('Failed to move backup file over real file')

        print(datetime.now(), fduration, len(M), 'of', len(colors), f'(fail_count: {fail_count}, {red_count})', timestamps_in_flight)
        red_count = 0
        fail_count = 0
      else:
        if fcolor in M:
          red_count += 1
        fail_count += 1
        # print(datetime.now(), fduration, fail_count, len(M))
        # print('.', end='')

      # # clear out history
      # for m in range(latest_cutoff, timestamp):
      #   if m in timestamps_received:
      #     timestamps_received.discard(m)
      #     timestamp_to_diff.pop(m, None)
      #   else:
      #     break
      # latest_cutoff = m

    except ray.exceptions.RayError as e:
      print('ignoring ray error', e)
      continue

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

  partial_filename = f'partial_pa_{perm_len}_{pa_distance}.txt'
  M = dict()
  if os.path.exists(partial_filename):
    with open(partial_filename, 'r') as f:
      for line in f:
        if not line.strip():
          continue

        row = list(map(int, line.strip().split()))
        a = tuple(e // pa_distance for e in row)
        M[a] = row

    print(f'Resuming with {len(M)} rows')

  state = find_it(perm_len, pa_distance, eps, M=M)
  if state is None:
    print('Failed')
    exit(1)

  print('Success')

  with open(f'hresults/pa_{perm_len}_{pa_distance}.txt', 'w+') as f:
    for v in state.values():
      f.write(' '.join(map(str, v)) + '\n')
