from collections import Counter
from math import factorial


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


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def dump_pa(A,filename,verbose=False):
  verbose and print (f'Writting {len(A)} rows to {filename}')
  with open(filename, 'w+') as f:
    for row in A:
      f.write(' '.join(map(str, row)) + '\n')


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def distance(u, v):
  return max(abs(a-b) for a,b in zip(u,v))


def block_distance(A):
  d = float('inf')
  for ux in range(len(A)):
    for vx in range(ux):
      d = min(d, distance(A[ux], A[vx]))
  return d


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True
       

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


def nCr(n, k):
  return factorial(n) // factorial(k) // factorial(n-k)

