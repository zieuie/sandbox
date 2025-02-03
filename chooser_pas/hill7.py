# Sudborough's January 31 Algorithm with a tweaked score, and disturbing instead of halting
# And permuting the whole HIGH(i) instead of just transposing, but choosing candidate permutations randomly
# And not measuring the whole PA's separation each time
# And doing this queueing thing to try multiple branches

import itertools as it
import random
from collections import Counter
from copy import deepcopy
from datetime import datetime


def separated(u, v, d):
  for a,b in zip(u,v):
    if abs(a-b) >= d:
      return True
  return False


def disagreement_counter(pa, d):
  ret = []
  c = Counter()
  for vx, v in enumerate(pa):
    for ux in range(vx):
      u = pa[ux]
      separated = False
      for a,b in zip(u,v):
        if abs(a-b) >= d:
          separated = True
          break
      if not separated:
        ret.append((ux,vx))
        c.update([ux])
        c.update([vx])
  return c


def load_pa():
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def dumb_pa(n, k):
  m = n-k
  sr = set(range(n))
  A = []
  H = list(it.combinations(list(range(n)), k))
  L = [sorted(sr-set(e)) for e in H]
  for ps in H:
    highs = tuple(range(m))
    lows = tuple(range(m, n))
    h, l = 0, 0
    row = []
    for i in range(n):
      if i in ps:
        row.append(highs[h])
        h += 1
      else:
        row.append(lows[l])
        l += 1
    A.append(row)
  return A, H, L


# Find the best permutation of A[i]
def random_good_permutation(A, start, i, d, old_score, loops=10):
  target = [e for e in start]
  for _ in range(loops):
    random.shuffle(target)
    pot = [e for e in A[i]]
    for u,v in zip(start, target):
      pot[u] = A[i][v]

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += 1

    if w > old_score:
      return w, start, target
  else:
    return 0, None, None


# Find the best permutation of A[i]
def best_permutation(A, start, i, d, old_score, loops=10):
  for target in it.permutations(start):
    pot = [e for e in A[i]]
    for u,v in zip(start, target):
      pot[u] = A[i][v]

    w = 0
    for x, e in enumerate(A):
      if x != i and separated(pot, e, d):
        w += 1

    if w > old_score:
      return w, start, target
  else:
    return 0, None, None


def disturb(A, H, L, d):
  # Randomly pick the top 10 worst
  s = [[] for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].append(vx)
        s[vx].append(ux)
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  i = random.randrange(10)
  i = q[i][1]

  if random.random() < .5:
    # Permute the high symbols
    ps = [e for e in H[i]]
    qs = [e for e in H[i]]
  else:
    # Permute the low symbols
    ps = [e for e in L[i]]
    qs = [e for e in L[i]]

  ret = [e for e in A[i]]
  random.shuffle(qs)
  for u,v in zip(ps,qs):
    ret[u] = A[i][v]
  return A[:i] + [ret] + A[i+1:]


def scorefun(pa, d):
  ret = [0]*len(pa)
  for vx in range(len(pa)):
    for ux in range(vx):
      if vx != ux and separated(pa[vx], pa[ux], d):
        ret[vx] += 1
        ret[ux] += 1
  return ret
  

def main(n, k, d):
  # Get a starting PA
  Arr, H, L = dumb_pa(n, k)
  try:
    Arr = load_pa()
  except FileNotFoundError:
    pass

  # set up the queue
  max_queue_size = 10
  dc = disagreement_counter(Arr, d)
  q = [(Arr, len(dc), sum(dc.values()), 0, scorefun(Arr, d))]
  best_pa, best_coverage, best_score, _, _ = q[0]

  # helper
  def enqueue(pa, uncovered, disagreements, depth, scores=None):
    # print('enqueue', len(pa), uncovered, disagreements)
    if len(q)+1 > max_queue_size:
      # for e,i in reversed(sorted( (e[2],i) for i,e in enumerate(q) )):
      for i,e in enumerate(q):
        if e[2] > disagreements:
          break
      else:  # here be dragons
        return False
      del q[i]
    q.append((pa, uncovered, disagreements, depth, scores or scorefun(pa, d)))
    return True

  # loop
  iteration_count = -1
  try:
    while q:
      # get a node
      iteration_count += 1
      onode = q.pop(0)
      # q.append(node)
      pa, uncovered, disagreements, depth, scores = onode

      # status
      if not disagreements:
        print('Done!')
        return pa
      if iteration_count % 10 == 0:
        print ([e[2] for e in q])
        print(datetime.now(), 'Iteration:', iteration_count, 'Uncovered:', uncovered, 'Disagreements:', disagreements, 'Best score:', best_score, 'Best coverage:', best_coverage, 'of', len(pa))

      # put good children in the queue
      # Try some random rows to improve
      anychild = False
      for _ in range(10):
        i = random.randrange(len(pa))
        score = scores[i]

        # Find some way to improve this row
        separations, one, two = random_good_permutation(pa, H[i], i, d, score, 10)
        if separations <= score:
          separations, one, two = random_good_permutation(pa, L[i], i, d, score, 10)

        # no improvement found. move on
        if separations <= score:
          continue

        # construct the PA
        nex = [e for e in pa[i]]
        for u,v in zip(one,two):
          nex[u] = pa[i][v]
        child = pa[:i] + [nex] + pa[i+1:]

        # enqueue
        dc = disagreement_counter(child, d)
        if not dc:
          print('Done!')
          return child
        node = child, len(dc), sum(dc.values()), 0
        if node[2] < best_score:
          best_pa, best_coverage, best_score, _ = node
        if enqueue(*node):
          anychild = True
          break

      # enqueue(*onode)
      # enqueue(*onode)
      if not anychild and depth == 0:
        enqueue(*onode)
        child = disturb(pa, H, L, d)
        dc = disagreement_counter(child, d)
        if not dc:
          print('Done!')
          return child
        node = child, len(dc), sum(dc.values()), 1
        enqueue(*node)
        # q.append(node)

  except KeyboardInterrupt:
    return best_pa
  
  return best_pa


if __name__ == '__main__':
  # The original PA is size m
  filename = 'dump99.txt'
  # pa = main(10, 5, 5)
  pa = main(12, 6, 6)
  with open(filename, 'w+') as f:
    disagreements = disagreement_counter(pa, 5)
    f.write(f'# Disagreements: {len(disagreements)} {disagreements}\n\n')
    for row in pa:
      f.write(' '.join(map(str, row)) + '\n')

