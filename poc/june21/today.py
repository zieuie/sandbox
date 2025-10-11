from lib import *
import json
import itertools as it
from collections import *
import random


class DisjointSet:
    def __init__(self, elements):
        # parent[i] stores the parent of element i.
        # Initially, each element is its own parent (representative).
        self.parent = {element: element for element in elements}
        # Optional: Store the size of each set (for union by size/rank optimization)
        self.size = {element: 1 for element in elements} 

    def find(self, i):
        # Path compression optimization: Make all nodes on the path point directly to the root.
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i, j):
        root_i = self.find(i)
        root_j = self.find(j)

        if root_i != root_j:
            # Union by size/rank optimization: Attach the smaller tree to the root of the larger tree.
            if self.size[root_i] < self.size[root_j]:
                self.parent[root_i] = root_j
                self.size[root_j] += self.size[root_i]
            else:
                self.parent[root_j] = root_i
                self.size[root_i] += self.size[root_j]
            return True # Union occurred
        return False # Elements were already in the same set

    def connected(self, i, j):
        return self.find(i) == self.find(j)


def make_array(n,d):
  # n, d = 10, 5
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

# def worst_offenders_first(A,D,n,d):
#   W = []
#   for 

from pyvis.network import Network
def vis(D,n):
  net = Network(notebook = True, cdn_resources = "remote",
                  bgcolor = "#222222",
                  font_color = "white",
                  height = "750px",
                  width = "100%",
  )
  net.add_nodes(list(range(len(D))))
  # net.add_edges(edges)
  for u,vs in enumerate(D):
    for v in vs:
      # net.add_edge(u,v, width=10, physics=False)
      net.add_edge(u,v, width=10)

  net.show(f"graph_{n}_{n//2}.html")

def nucleus(D):
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

  for k,v in sizes.items():
    print (f'Connected component size: {k}  Count: {v}')
  
  return components


def go(n, d):
  # n, d = 6, 3   # 14 of 20
  # n, d = 8,4    # 42 of 70
  # n, d = 10,5   # 132 of 252,   least offender 106 of 252
  # n, d = 12, 6  # 429 of 924,  least offender 328 of 924, random 339
  # n, d = 14, 7

  A, B = make_array(n,d)
  print(f'Starting with {len(A)} rows')
  guided = False

  D, good, bad = make_distances(A,n,d)
  print('Good pairs have', good, 'out of', len(A) * (len(A)-1) // 2, 'which is', good / (len(A) * (len(A)-1) // 2), 'percent')

  # vis(D,n)
  nucleus(D)

  return

  while True:
    D, good, bad = make_distances(A,n,d)
    c = Counter()
    for a,l in zip(A,D):
      c.update(l)

    if not c:
      break
    elif guided:
      for x, a,b,l in zip(range(len(A)), A,B,D):
        print(f'{x:2}', a,b, l)

      print ('Worst offenders:')
      for k,v in reversed(c.most_common(len(c))):
        print(f'Offender: {k}  Offended: {v}')

      print('len(A), good, bad')
      print(len(A)*(len(A)-1)//2, good, bad)

      usr = input('Row to remove:')
      try:
        if usr == 'exit':
          break
        usr = int(usr)
        if not (0 <= usr < len(A)):
          raise ValueError()
      except:
        usr = input('Row to remove:')
    else:
      # delete pairs
      # pots = [x for x,e in enumerate(D) if e]
      # u = random.choice(pots)
      # v = random.choice(D[u])
      # if u < v:
      #   u, v = v, u
      # del A[u]
      # del A[v]

      # delete pairs with worst offender
      u = c.most_common(len(c))[0][0]
      v = random.choice(D[u])
      if u < v:
        u, v = v, u
      del A[u]
      del A[v]

      # random offender
      # pots = [x for x,e in enumerate(D) if e]
      # usr = random.choice(pots)

      # worst offender
      # usr, freq = c.most_common(len(c))[0]
      
      # least offender
      # usr, freq = c.most_common(len(c))[-1]
      
      # print (c.most_common(len(c)))
      # print ('Removing', usr, freq, len(A)-1)

    # del A[usr]

  print (f'Exiting with {len(A)} rows')
  with open(f'pa_experimental_{n}_{d}.txt', 'w+') as f:
    # json.dump(A, f)
    for row in A:
      f.write(' '.join(map(str, row)) + '\n')

  # worst_offender = print(c.most_common(1)[0][0])

def main():
  for d in it.count(3):
    print()
    print(f'Attempting (n,d) = ({2*d}, {d})')
    go(2*d,d)
    # input()

# main()


print(3432   * 1
+ 924   * 2
+ 252   * 5
+ 70   * 14
+ 20   * 42
+ 6   * 132
+ 2   * 429
+ 1   * 2860)
