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


import re

filename = argv[1]
A = load_pa(filename)
print(f'Loaded {len(A)} rows')
numbers = re.findall(r'\d+', filename)

try:
  n, d = int(numbers[0]), int(numbers[1])
  if n != len(A[0]) or d >= n or d < 1:
    raise ValueError()
except:
  print ('Unable to deduce n and d from filename')
  exit(1)


# find bundles of angry counterparts where the bundle is separated
def judge(happy, angry):
  sofar = []
  def recur(i=0):
    # print('recur',i)
    if i>=len(angry):
      yield deepcopy(sofar)
      return

    for u in infills(n,d,[e//d for e in angry[i]]):
      # print('>'*(i+1), u)
      for v in it.chain(sofar, happy[:15], happy[-15:]):
        if not separated(u,v,d):
          break
      else:
        sofar.append(u)
        yield from recur(i+1)
        sofar.pop()
  yield from recur()


# real stuff starts here

foes = init_foes(A,n,d)
lut = init_problems(A, d, foes)

# angry rows aren't fully separated from the rest
happy = [A[k] for k,v in enumerate(lut) if not v]
angry = [A[k] for k,v in enumerate(lut) if v]

# rank bundles by their separation from happiness
# meep = Counter()
asdf = []
for pot in judge(happy, angry):
  beefs = []
  for u in pot:
    for vx, v in enumerate(happy):
      if 15 <= vx <= len(happy)-15 and not separated(u,v,d):
        beefs.append(vx)
  if len(beefs) < 5:
    asdf.append((len(beefs),beefs,pot))
  # meep.update([w])
# print(meep)
asdf.sort()

# take the bottom few
# asdf = asdf[:4]
for w, beefs, mad in asdf:
  print('meep', len(happy), len(beefs), len(mad))
  newhap = [v for k,v in enumerate(happy) if k not in beefs] + mad
  newang = [happy[k] for k in beefs]
  print(w, beefs)
  
  for pot in judge(newhap, newang):
    score = []
    for u in pot:
      for vx, v in enumerate(newhap):
        if 15 <= vx <= len(newhap)-15 and not separated(u,v,d):
          score.append(vx)
    if not score:
      print('Found!')
      A = newhap + pot
      print(len(A), verify(A,d))
