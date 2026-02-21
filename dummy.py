from random import randrange, seed

import itertools as it

import ray
ray.init() # Only call this once.

@ray.remote
class Counter(object):
    def __init__(self):
      seed(hash(self))

    def increment(self, givens):
      return givens, {randrange(100):randrange(100) for _ in range(randrange(1, 10))}

counters = [Counter.remote() for _ in range(2)]
futures = {c.increment.remote(0):c for c in counters}
print(list(map(type, counters)))

M = dict()

past = {0: set()}
# for timestamp in range(1, 10):
for timestamp in it.count(1):
  # print status
  print(timestamp, sorted(M.keys()))

  if len(M) == 100:
    print('done!')
    break

  # get the changes
  ready, not_ready = ray.wait(list(futures.keys()), num_returns=1)
  future = ready[0]
  worker = futures.pop(future)
  lamp, pot = ray.get(future)

  # fire the next work
  futures[worker.increment.remote(timestamp)] = worker
  
  # check history
  good = True
  s = set(pot.keys())
  for t in range(lamp, timestamp):
    p = past.get(t)
    if p is None or p & s:
      print('t bad', t, p, s)
      good = False
      break
  else:
    print('good', timestamp, s)

  # commit and update history
  if good:
    M.update(pot)
    past[timestamp] = s
  else:
    past[timestamp] = set()
