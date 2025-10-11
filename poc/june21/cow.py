import itertools as it
from collections import defaultdict

pa33 = [[0,1,2]]

pa63 = [
  [3, 5, 4, 2, 1, 0],
  [3, 4, 1, 5, 0, 2],
  [3, 5, 2, 1, 4, 0],
  [5, 4, 2, 1, 0, 3],
  [5, 2, 4, 3, 1, 0],
  [5, 2, 3, 1, 4, 0],
  [4, 2, 5, 1, 0, 3],
  [4, 2, 1, 5, 3, 0],
  [5, 1, 0, 3, 2, 4],
  [5, 2, 1, 0, 4, 3],
  [2, 3, 4, 5, 1, 0],
  [1, 3, 5, 2, 4, 0],
  [2, 5, 4, 1, 0, 3],
  [0, 3, 2, 4, 5, 1],
  [2, 4, 1, 3, 0, 5],
  [2, 4, 1, 0, 3, 5],
  [1, 0, 5, 3, 4, 2],
  [2, 1, 3, 4, 0, 5],
  [2, 1, 5, 0, 4, 3],
  [2, 1, 0, 4, 3, 5],
]

def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def meep():
  # Find permutations that are not separated from themselves under rotation

  def classify(p):
    return tuple(e//3 for e in p)

  # p = [5, 3, 4, 0, 2, 1, 6, 7, 8]

  f = lambda p: p[3:] + p[:3]
  d = defaultdict(lambda: 0)
  for p in it.permutations(list(range(9))):
    pf = f(p)
    pff = f(pf)

    if not separated(p, pf, 3) or not separated(p, pff, 3) or not separated(pf, pff, 3):
      d[classify(p)] += 1
      # print(p)

  for k,v in sorted(d.items()):
    print(k,v)


# An application function
def apply_symmetry(row, sym):
  ret = [0]*len(row)
  for s,r in zip(sym,row):
    ret[s] = r
  return ret

# Representatives of enweaving operations
def make_reps(n,k,rot,f):
  R = set()
  ret = []
  for ps in it.combinations(list(range(n)), k):
    row = [0]*n
    for p in ps:
      row[p] = 1
    row = tuple(row)
    for _ in range(n//rot):
      row = f(row)
      if row in R:
        break
    else:
      R.add(row)
      ret.append(ps)
  return ret

# We'll try to make (6,3) using symmetry
import random

# n, d, k, rot, pre = 6, 3, 3, 3, pa33
n, d, k, rot, pre = 9, 3, 3, 3, pa63
f = lambda p: p[3:] + p[:3]

# Make the weaving representatives
R = make_reps(n, k, rot, f)
for idx,r in enumerate(R):
  print (idx,r)

# Mod the pre
for p in pre:
  asdf = [[] for _ in range(n//k)]
  for idx,e in enumerate(p):
    asdf[e//k].append(idx)
  print(asdf)

def all_weaves(rep, mod):
  groups = []
  for start in range(0, n, k):
    groups.append(list(range(start,start+k)))

  sofar = [0]*n
  def recur(g=0):
    if g*k >= n:
      for qs in it.permutations(groups[g]):

    else:
      
  yield from recur()

# # The first row
# first = list(range(n))
# random.shuffle(first)