import itertools as it
from copy import deepcopy
from datetime import datetime
from collections import Counter
import itertools as it
import random
import math
from sys import argv



HELP_STR = '''
Usage:
  pypy3 hill.py n d

Creates an (n,d)-PA of size (n choose d), where
each row has its d highest symbols in a different of the
(n choose d) positions that they could be arranged in.
'''


def ceildiv(n,d):
  # return n//d + int(bool(n%d))
  return math.ceil(n/d)


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


def update_diffs(A, n, d, lut, i, row, gain, loss):
  A[i] = row
  # oldlut = deepcopy(lut)

  # lutmap[len(lut[i])].discard(i)
  # lutmap[len(lut[i]) - len(gain) + len(loss)].add(i)

  for x in gain:
    # lutmap[len(lut[x])].discard(x)
    # lutmap[len(lut[x])-1].add(x)
    lut[i].discard(x)
    lut[x].discard(i)
  for x in loss:
    # lutmap[len(lut[x])].discard(x)
    # lutmap[len(lut[x])+1].add(x)
    lut[i].add(x)
    lut[x].add(i)

  # newfoes = init_foes(A,n,d)
  # newlut = init_problems(A,d,newfoes)
  # if newlut != lut:
  #   print('---')
  #   print('gain', gain)
  #   print('loss', loss)
  #   print('oldlut', oldlut)
  #   print('newlut', newlut)
  #   print('lut', lut)
  #   print('Oy!')
  #   input()

def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


def meep(mapping):
  pop = [k for k,v in mapping.items() if v]
  weights = [(e+1)**2 for e in pop]

  size = random.choices(pop, weights=weights, k=1)[0]
  return random.choice(list(mapping[size]))


def meekly_disturb(A,n,d, lut, foes, lutmap, w, tolerance):
  while True:
    i = meep(lutmap)
    # i = random.randrange(len(A))
    # i = random.choice([e for e,v in enumerate(lut) if v or e<5])
    one = pull_group(A[i],n,d,random.randrange(ceildiv(n,d)))
    two = [e for e in one]
    random.shuffle(two)
    row = apply_permutation(A[i], one, two)
    gain, loss = eval_permutation(A, i, d, row, lut, foes)
    if w - 2*len(gain) + 2*len(loss) <= tolerance:
      return i, row, gain, loss


def gently_disturb(A,n,d, lut, foes, lutmap):
  while True:
    i = meep(lutmap)
    one = pull_group(A[i],n,d,random.randrange(ceildiv(n,d)))
    two = [e for e in one]
    random.shuffle(two)
    row = apply_permutation(A[i], one, two)
    gain, loss = eval_permutation(A, i, d, row, lut, foes)
    if len(gain) >= len(loss):
      return i, row, gain, loss


def greatly_disturb(A,n,d,lut,foes, lutmap):
  i = meep(lutmap)
  one, two = [], []
  for x in range(ceildiv(n,d)):
    src = pull_group(A[i], n, d, x)
    dst = [e for e in src]
    random.shuffle(dst)
    one.extend(src)
    two.extend(dst)

  row = apply_permutation(A[i], one, two)
  gain, loss = eval_permutation(A, i, d, row, lut, foes)
  return i, row, gain, loss


from collections import Counter, defaultdict
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


def smart_hill(A, n, d):
  foes = init_foes(A,n,d)
  lut = init_problems(A, d, foes)

  best_pa = deepcopy(A)
  best_lut = deepcopy(lut)
  best_foes = deepcopy(foes)
  best_score = float('inf')
  last_printed_score = float('inf')
  last_tweak = 0

  try:
    for it_count in it.count():
      w = sum(map(len, lut))
      should_print = it_count % 10000000 == 0

      lutmap = defaultdict(set)
      for k,v in enumerate(lut):
        if v:
          lutmap[len(v)].add(k)

      # should_print = it_count % 10000 == 0
      if w < best_score:
        best_pa = deepcopy(A)
        best_lut = deepcopy(lut)
        best_foes = deepcopy(foes)
        should_print = should_print or last_printed_score - best_score > 100 or best_score < 100
        if best_score != float('inf'):
          yield A, w
        best_score = w
        if w == 0:
          return

      # if not it_count % 10000:
      #   print(it_count, w, it_count-last_tweak, ', '.join(f'({k}:{",".join(map(str, v))})' for k,v in enumerate(lut) if v), lutmap)

      if should_print:
        coverage = sum(1 for v in lut if not v)
        print()
        print(datetime.now(), f'P({n}, {d})', 'Iteration:', it_count, 'Score:', w, 'Best:', best_score, 'Coverage:', coverage, 'of', len(lut), 'Last tweak:', last_tweak)
        last_printed_score = best_score

      # if w > best_score and it_count - last_tweak > 50000:
      #   # print ('.', end='')
      #   print(', '.join(f'{k}:{" ".join(map(str, v))}' for k,v in enumerate(lut) if v))
      #   A = deepcopy(best_pa)
      #   lut = deepcopy(best_lut)
      #   foes = deepcopy(best_foes)
      #   lutmap = deepcopy(best_lutmap)
      #   last_tweak = it_count
      # elif w == best_score and it_count - last_tweak > 10000:
      #   i, row, gain, loss = greatly_disturb(A,n,d,lut,foes,lutmap)
      #   last_tweak = it_count
      # else:
        # i, row, gain, loss = gently_disturb(A,n,d,lut,foes,lutmap)
        # if len(gain) > len(loss):
        #   last_tweak = it_count


      # i, row, gain, loss = gently_disturb(A,n,d,lut,foes,lutmap)
      # diff = 4 if 100000 < it_count - last_tweak < 1000000 else 2
      i, row, gain, loss = meekly_disturb(A,n,d,lut,foes,lutmap,w,w)
      # i, row, gain, loss = meekly_disturb(A,n,d,lut,foes,lutmap,w,4)
      # i, row, gain, loss = meekly_disturb(A,n,d,lut,foes,lutmap,w,min(4,max(w,w+diff)))
      # i, row, gain, loss = meekly_disturb(A,n,d,lut,foes,lutmap,w,w)
      if len(gain) > len(loss):
        last_tweak = it_count
      update_diffs(A, n, d, lut, i, row, gain, loss)

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


# you can only use dork hill if all rows are separated except A[-1]
# def dork_hill(A,n,d):
#   foes = init_foes(A,n,d)
#   lut = init_problems(A, d, foes)

#   v = A[-1]
#   vx = len(A)-1

#   def recur(ux):
#     for u in infills(n,d,A[ux]):
#       if not separated(u,v,d):
#         continue
#       for i in range(ux):
#         if not separated()


      # gain, loss = eval_permutation(A, ux, d, row, lut, foes)
      # if len(gain) >= len(loss):
        # return i, row, gain, loss

  # yield from recur(vx-1)


def weave_template(n,d):
  # weave template
  A = [[ceildiv(n,d)-1]*(n%d or d)]
  for x in range(ceildiv(n,d)-1):
    B = []
    for a in A:
      for ps in it.combinations(list(range(len(a)+d)), d):
        nex = []
        l = 0
        for i in range(len(a)+d):
          if i in ps:
            nex.append(x)
          else:
            nex.append(a[l])
            l += 1
        B.append(nex)
    A = B
  return A


def kitty(A,n,d):
  T = weave_template(n,d)

  lut = dict()
  for i,t in enumerate(T):
    lut[tuple(t)] = i

  B = [None for _ in T]
  for x,u in enumerate(A):
    B[lut[tuple(e//d for e in u)]] = u

  for x,u in enumerate(B):
    if u is None:
      pots = [iter(range(e*d, (e+1)*d)) for e in range(n//d + int(bool(n%d)))]
      row = [next(pots[e]) for e in T[x]]
      yield row


import re
import os
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

  pots = list(kitty(A,n,d))
  print(len(A))
  print(len(pots))

  if verify(A,d):
    A.append(random.choice(pots))
 
  for pa, w in smart_hill(A, n, d):
    if pa is None:
      print ('Cancelled')
      break
    with open(f'partial_{n}_{d}_{len(pa)}_{w}.txt', 'w+') as f:
      for row in pa:
        f.write(' '.join(map(str, row)) + '\n')
    print(f'Wrote score {w}')

  if verify(A, d):
    print (f'Verified {len(A)} rows')
  else:
    print ('Failed to verify')


main()


# n,d = 8,3
# A = dumb_pa(5,3)
# B = enweave(A, 8, 3)
# for row in B:
#   print(row)