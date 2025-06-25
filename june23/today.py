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


def greedy_chebyfy(A,n,d,guided=False,B=None):
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

  return A


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


# # Do the thing for n,d
# def go(n, d):
#   A, B = make_array(n,d)
#   print(f'Starting with {len(A)} rows')
#   D, good, bad = make_distances(A,n,d)
#   print('Good pairs have', good, 'out of', len(A) * (len(A)-1) // 2, 'which is', good / (len(A) * (len(A)-1) // 2), 'percent')

#   vis(D,n)
#   nucleus(D)


# def main():
#   for d in it.count(3):
#     print()
#     print(f'Attempting (n,d) = ({2*d}, {d})')
#     go(2*d,d)

# main()

def block_distance(A):
  d = float('inf')
  for ux in range(len(A)):
    for vx in range(ux):
      d = min(d, distance(A[ux], A[vx]))
  return d

def do_shift(row, f):
  return [f.get(e, e) for e in row]

def make_first_component():
  # define the mapping
  f = dict()
  for x in range(4):
    f[(x+1) % 4] = x

  # load the PA
  Z = load_pa('pa_6_choose_3_verified.txt')
  A = []
  for row in Z:
    A.append([0] + [e+1 for e in row] + [7])

  B = []
  for row in A:
    B.append(do_shift(row, f))
  A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e+1 for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


def make_second_component():
  # define the mapping
  f = {i:e for i,e in enumerate([1,2,4,5])}

  # load the PA
  Z = load_pa('pa_4_choose_2_verified.txt')
  A = []
  for row in Z:
    A.append([0] + do_shift(row, f) + [6,7,3])

  # define the mapping
  f = dict()
  for x in range(3):
    f[(x+1) % 3] = x

  B = []
  for row in A:
    B.append(do_shift(row, f))
  A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e+1 for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


def make_third_component():
  # define the mapping
  f = {i:(e-1) for i,e in enumerate([3, 4, 6, 7])}

  # load the PA
  Z = load_pa('pa_4_choose_2_verified.txt')
  A = []
  for row in Z:
    A.append([4,0,1] + do_shift(row, f) + [7])

  # define the mapping
  f = dict()
  r = range(1,4)
  for i,e in enumerate(r):
    f[e] = r[(i-1)%len(r)]

  B = []
  for row in A:
    B.append(do_shift(row, f))
  A=B

  f = dict()
  r = [7, 4, 3]
  for i,e in enumerate(r):
    f[e] = r[(i-1)%len(r)]

  B = []
  for row in A:
    B.append(do_shift(row, f))
  A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e+1 for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


make_first_component()
make_third_component()
make_second_component()



'''
5 1 2 3 4 6 7 8
5 1 2 3 6 4 7 8
5 1 2 3 6 7 4 8
5 1 2 6 3 4 7 8
5 1 2 6 3 7 4 8
5 1 2 6 7 3 4 8
'''


