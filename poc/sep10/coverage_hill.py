import itertools as it
import math
import random
import re
from collections import *
from copy import deepcopy
from datetime import datetime
from sys import argv


def ceildiv(n,d):
  return n//d + int(bool(n%d))


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def enweave(A, n, d):
  ret = []
  lows = list(range(d))
  for row in A:
    for ps in it.combinations(list(range(n)), d):
      random.shuffle(lows)
      l, h = 0, 0
      new = []
      for i in range(n):
        if i in ps:
          new.append(lows[l])
          l += 1
        else:
          new.append(row[h]+d)
          h += 1
      ret.append(new)
  return ret


def dumb_pa(n, d):
  k = n%d
  A = []
  for ps in it.combinations(list(range(n)), k):
    lows = tuple(range(n-k))
    highs = tuple(range(n-k, n))
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
  return A


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  i = 0
  while i < len(u) and (u[i]-v[i])**2 < dd:
    i += 1
  return i < len(u)


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


def init_foes(A,n,d):
  lut = [set() for _ in A]
  for vx,v in enumerate(A):
    v = [e//d for e in v]
    for ux in range(vx):
      sep = False
      for dx,y in zip(A[ux],v):
        if abs(dx//d - y) > 1:
          sep = True
          break
      if not sep:
        lut[ux].add(vx)
        lut[vx].add(ux)
  return lut


def init_problems(A, d, foes):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in foes[vx]:
      if ux < vx and not separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, ux, d, upot, lut, foes):
  gain = []
  loss = []
  umad = lut[ux]
  for vx in foes[ux]:
    if ux == vx:
      continue
    v = A[vx]
    sep = separated(upot, v, d)
    if sep and vx in umad:
      gain.append(vx)
    elif not sep and vx not in umad:
      loss.append(vx)
  return gain, loss


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


def meep(mapping):
  pop = [k for k,v in mapping.items() if v]
  weights = [(e+1)**2 for e in pop]

  size = random.choices(pop, weights=weights, k=1)[0]
  return random.choice(list(mapping[size]))


def smart_hill(A, n, d):
  foes = init_foes(A,n,d)
  lut = init_problems(A, d, foes)

  best_pa = deepcopy(A)
  best_score = float('inf')
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      # w = sum(map(len, lut)) * 1000000 + sum(1 for _ in filter(None, lut))
      # w = sum(sum(abs(u-v) for v in vs) for u,vs in enumerate(lut))
      w = make_score(lut)
      should_print = it_count % 10000000 == 0
      # print(it_count, w, Counter(map(len, lut)))

      lutmap = defaultdict(set)
      for k,v in enumerate(lut):
        if v:
          lutmap[len(v)].add(k)

      if w < best_score:
        best_pa = deepcopy(A)
        # should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        should_print = True
        if best_score != float('inf'):
          yield A, w
        best_score = w
        if w == 0:
          return

      if should_print:
        coverage = sum(1 for v in lut if not v)
        c = Counter(map(len, lut))
        c2 = [0]*(1+max(c.keys()))
        for k,v in c.items():
          c2[k] = v
        print()
        print(datetime.now(), f'P({n}, {d})', 'Iteration:', it_count, c2)
        last_printed_score = best_score

      while True:
        i = random.choice([i for i,v in enumerate(lut) if v])
        one = pull_group(A[i],n,d,random.randrange(ceildiv(n,d)))
        two = [e for e in one]
        random.shuffle(two)
        row = apply_permutation(A[i], one, two)
        gain, loss = eval_permutation(A, i, d, row, lut, foes)

        nex = deepcopy(lut)
        for x in gain:
          nex[i].discard(x)
          nex[x].discard(i)
        for x in loss:
          nex[i].add(x)
          nex[x].add(i)
        pot = make_score(nex)
        if pot <= w:
          break

      A[i] = row
      lut = nex
      if pot < w:
        w = pot
        last_tweak = it_count
  
      # i, row, gain, loss = meekly_disturb(A,n,d,lut,foes,lutmap,w,w)
      # if len(gain) > len(loss):
      #   last_tweak = it_count
      # update_diffs(A, n, d, lut, i, row, gain, loss)

  except KeyboardInterrupt:
    yield None, None
    pass

  yield best_pa, best_score


def infills(n,d,t):
  sofar = []
  def recur():
    if len(sofar) >= n:
      yield sofar
      return
    c = t[len(sofar)]
    for x in range(c*d, min(n, (c+1)*d)):
      if x not in sofar:
        sofar.append(x)
        yield from recur()
        sofar.pop()
  yield from recur()


def main():
  filename = argv[1]
  A = load_pa(filename)
  numbers = re.findall(r'\d+', filename)

  try:
    n, d = int(numbers[0]), int(numbers[1])
    if n != len(A[0]) or d >= n or d < 1:
      raise ValueError()
  except:
    print ('Unable to deduce n and d from filename')
    exit(1)
 
  for pa, w in smart_hill(A, n, d):
    if pa is None:
      print ('Cancelled')
      break
    # with open(f'moofour_partial_{n}_{d}_{len(pa)}.txt', 'w+') as f:
    with open(f'test_8_3_three.txt', 'w+') as f:
      for row in pa:
        f.write(' '.join(map(str, row)) + '\n')
    # print(f'Wrote score {w}')

  # if verify(A, d):
  #   print (f'Verified {len(A)} rows')
  # else:
  #   print ('Failed to verify')


def make_score(lut):
  return sum( map(len,lut) ) - 2**sum( 1 for v in lut if not v )
  # return sum( sum(a*b*b*a for b in bs) for a,bs in enumerate(lut) ) - 2**sum( 1 for v in lut if not v )
  # return sum( 1 for v in lut if v )


main()


# n,d = 8,3
# A = dumb_pa(5,3)
# B = enweave(A, 8, 3)
# for row in B:
#   print(row)