
from lib import *
import json
import itertools as it
from collections import *
import random
# from pyvis.network import Network  # pyviz doesn't build today?
import networkx as nx
import matplotlib.pyplot as plt


def dump_pa(A,n,d):
  print (f'Writting array ({n},{d}) with {len(A)} rows')
  with open(f'pa_experimental_{n}_{d}_{len(A)}.txt', 'w+') as f:
    for row in A:
      f.write(' '.join(map(str, row)) + '\n')


def make_array(n,d):
  A = []
  B = []
  for ps in it.combinations(list(range(n)), d):
    t = []
    r = []
    l, h = 0,n//2
    for x in range(n):
      if x in ps:
        t.append(l)
        r.append(0)
        l += 1
      else:
        t.append(h)
        r.append(1)
        h += 1
    # print(t, r)
    A.append(t)
    B.append(r)
  return A,B


def make_distances(A,n,d):
  D = [[] for _ in range(len(A))]
  good = 0
  bad = 0
  for x in range(len(A)):
    for y in range(x):
      if separated(A[x], A[y], d):
        good += 1
      else:
        bad += 1
        D[x].append(y)
        D[y].append(x)
  return D, good, bad


def vis(D,n):
  # 1. Create a graph
  G = nx.Graph()
  for u,vs in enumerate(D):
    for v in vs:
      G.add_edge(u,v)

  # 2. Draw the graph
  nx.draw(G, with_labels=True, node_color='skyblue', node_size=1500, edge_color='gray', font_size=10)

  # 3. Display the plot
  plt.title(f"Lexicographic P({n}, {n//2})")
  plt.show()


def make_components(D):
  lut = DisjointSet(list(range(len(D))))
  for u,vs in enumerate(D):
    for v in vs:
      lut.union(u,v)
  
  components = defaultdict(list)
  for x in range(len(D)):
    y = lut.find(x)
    components[y].append(x)
  
  sizes = Counter()
  sizes.update(map(len,components.values()))

  # for k,v in sizes.items():
  #   print (f'Connected component size: {k}  Count: {v}')
  
  return components


def make_fixes(C,A,n):
  ret = dict()
  for x, (vx0, vs) in enumerate(C.items()):
    v0 = A[vx0]
    if len(vs) < 2:
      ret[vx0] = (v0, v0)
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
    ret[vx0] = (v0[:pre+1], v0[suf:])
  return ret

for n in range(4, 16, 2):
  d = n//2
  print()
  print()
  print(f'# {n},{d}')
  A,B = make_array(n,d)
  D,good,bad = make_distances(A,n,d)
  C = make_components(D)
  F = make_fixes(C,A,n)

  ret = []
  for vx0,(pre,suf) in F.items():
    l = len(C[vx0])
    if l == 1:
      continue
    mid = sorted(set(range(n)) - set(pre) - set(suf))
    ret.append((l,pre,suf,mid))
  ret.sort(reverse=True)

  for t in ret:
    print(*t)