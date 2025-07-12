
'''
length n, distance d, lexigraphic graph G(V,E).

Print the size of each connected component, along with
the prefixes/suffixes in common.


'''


from lib import *
from collections import *
import itertools as it



def make_array(n,d):
  A = []
  for ps in it.combinations(list(range(n)), d):
    t = []
    l, h = 0,n//2
    for x in range(n):
      if x in ps:
        t.append(l)
        l += 1
      else:
        t.append(h)
        h += 1
    A.append(t)
  return A


# { y: [x1, x2, ...] }
# where y is the disjoint set key
# x is an index in 
# So that y: Xs means there's some connected component y
# which consists of the rows and indices D
def make_components(A,d):
  lut = DisjointSet(list(range(len(A))))
  for x in range(len(A)):
    for ccid in range(x):
      if not separated(A[x], A[ccid], d):
        lut.union(x,ccid)
  
  components = defaultdict(list)
  for x in range(len(A)):
    ccid = lut.find(x)
    components[ccid].append(x)
  
  return components


# { x: (prefix, suffix), ... }
# x is the index of C
def make_fixes(C,A,n):
  ret = dict()
  for ccid, vs in C.items():
    v0 = A[ccid]
    if len(vs) < 2:
      ret[ccid] = (v0, v0)
    pre, suf = n-1, 0
    for vx in vs:
      v = A[vx]
      i = 0
      for pot in range(pre+1):
        if v[pot] == v0[pot]:
          i = pot
        else:
          break
      pre = i

      i = n-1
      for suf in reversed(range(suf, n)):
        if v[suf] == v0[suf]:
          i = suf
        else:
          break
      suf = i
    ret[ccid] = (v0[:pre+1], v0[suf:])
  return ret


def main(n):
  d = n//2

  A = make_array(n,d)
  C = make_components(A,d)
  F = make_fixes(C,A,n)

  ret = []
  for ccid,(pre,suf) in F.items():
    l = len(C[ccid])
    if l == 1:
      continue
    mid = sorted(set(range(n)) - set(pre) - set(suf))
    ret.append((l,pre,suf,mid))
  ret.sort(reverse=True)

  for t in ret:
    print(*t)


for n in range(4, 16, 2):
  print()
  print()
  print(f'# {n},{n//2}')
  main(n)