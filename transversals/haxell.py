import itertools as it
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from datetime import datetime


@dataclass(eq=True, frozen=True)
class Vertex:
  color: Any
  data: Any

  def edge(self, other):
    if self.color == other.color:
      return False
    for a,b in zip(self.data, other.data):
      if abs(a-b) >= pa_distance:
        return False
    return True

  def separated(self, other):
    if self.color == other.color:
      return False
    for a,b in zip(self.data, other.data):
      if abs(a-b) >= pa_distance:
        return True
    return False

  def pull(self, perm):
    rc = [0]*len(self.color)
    rd = [0]*len(self.data)
    for dst,src in enumerate(perm.data):
      rc[dst] = self.color[src]
      rd[dst] = self.data[src]
    return Vertex(tuple(rc), tuple(rd))

  def neighbors(self):
    for e in ident_neigh:
      yield e.pull(self)

  def __repr__(self):
    return repr(self.data)


# page 19
def find_it():
  global Ay0
  # M[class] = vertex
  M = dict()
  for i in it.count():
    for A in colors:
      if A not in M:
        break
    else:
      break

    print(datetime.now(), f'find_it iteration {i+1} of {len(colors)} |M| {len(M)}')

    Ay0 = A
    M = grow_transversal(M, A)
    if M is None:
      return None
  return M


def grow_transversal(M, A):
  M = deepcopy(M)

  # Xs = [{ color: {vertices} }, ...]
  Xs = [dict()]
  # Ys = [{ color: vertex }, ...]
  Ys = [dict()]
  l = 0
  while A not in M:
    X,Y = build_layer(M,Xs,Ys,dict(),dict(),l)

    Yllen = sum(map(len,Ys))
    if sum(map(len,X.values())) <= rho * Yllen:
      return None

    Xs.append(X)
    Ys.append(Y)
    l += 1
    while True:
      # Icount : int
      # Imap[color] = vertex
      Icount, Iany = immediate_count(M,Xs[l],l)
      if Icount <= mu * sum(map(len,Xs[l].values())):
        break

      if l == 1:
        M[Iany.color] = Iany
        return M

      to_delete = set()
      for w in Ys[l-1].values():
        if w.color not in Xs[l]:
          continue

        _, u = immediate_count(M, {w.color: Xs[l][w.color]},l)
        if u is None:
          continue

        M[w.color] = u
        to_delete.add(w.color)

      for a in to_delete:
        Ys[l-1].pop(a)

      Xs, Ys, l = superposed_build(M, Xs[:l], Ys[:l], l-1)


def superposed_build(M, Xs, Ys, l):
  i = 1
  while i <= l:
    X, Y = build_layer(M,Xs,Ys,Xs[i],Ys[i],i-1)
    if sum(map(len,X.values())) >= (1+mu)*sum(map(len,Xs[i].values())):
      Xs[i], Ys[i] = X,Y
      l = i
    i += 1
  return Xs[:l+1], Ys[:l+1], l


def build_layer(M,Xs,Ys,X,Y,l):
  global Ay0
  X = deepcopy(X)
  Y = deepcopy(Y)

  for A in (Ys[l] if l else [Ay0]):
    for v in from_color(A):
      if len(X.get(A, tuple())) >= U:
        break

      if not is_good(A,v,Xs,Ys,X,Y,l):
        continue

      # X = X u {v}
      X.setdefault(A, set()).add(v)

      # Y = Y u {u in M | uv in E}
      for u in M.values():
        if v.edge(u):
          Y[u.color] = u

  return X,Y


def is_good(A, v, Xs, Ys, X, Y, l):
  if v == Y.get(A):
    return False

  if v == Ys[l].get(A):
    return False

  if A in X and v in X[A]:
    return False

  # we don't have its neighbors
  for u in v.neighbors():
    if u == Y.get(u.color):
      return False

    if u.color in X and u in X[u.color]:
      return False

    for i in range(l+1):
      if u == Ys[i].get(u.color):
        return False
      if u.color in Xs[i] and u in Xs[i][u.color]:
        return False

  return True


def immediate_count(M,W,l):
  Icount = 0
  Iany = None

  for v in it.chain(*W.values()):
    for u in M.values():
      if u.edge(v):
        break
    else:
      if l != 1 or v.color == Ay0:
        Icount += 1
        Iany = v

  return Icount, Iany


def from_color(A):
  n,d = perm_len, pa_distance
  groups = []
  for x in range(int(math.ceil(n/d))):
    groups.append(list(range(d*x, min(n, d*(x+1)))))


  groups = list(map(list,map(it.permutations, groups)))
  for g in it.product(*groups):
    g = list(map(iter, g))
    data = [next(g[e]) for e in A]
    yield Vertex(color=tuple(A), data=tuple(data))


def make_colors():
  sofar = [0]*perm_len
  rem = dict()
  for x in range(int(math.ceil(perm_len/pa_distance))):
    rem[x] = list(range(pa_distance*x, min(perm_len, pa_distance*(x+1))))

  def recur(i):
    if i >= perm_len:
      yield tuple(sofar)
      return

    for k,v in rem.items():
      if v:
        pot = v.pop()
        sofar[i] = k
        yield from recur(i+1)
        v.append(pot)

  yield from recur(0)


def feasible_constants(r, eps):
  """
  Paper's example feasible triple (μ, U, ρ) for r>=2 and 0<eps<1:
    μ = eps/(10r), U = 10r/eps, ρ = eps/(10r)
  We use U as an integer threshold via ceil.
  """
  if r < 2:
    raise ValueError("Need r >= 2.")
  if not (0.0 < eps < 1.0):
    raise ValueError("This simple feasible choice assumes 0 < eps < 1.")
  mu = eps / (10.0 * r)
  U_real = (10.0 * r) / eps
  U = math.ceil(U_real)
  rho = eps / (10.0 * r)
  # return mu, U, rho
  return mu, U, rho


def make_ident_neigh():
  n,d = perm_len, pa_distance
  q = list(range(n))
  qt = [e//d for e in q]
  e = Vertex(tuple(qt),tuple(q))

  groups = []
  for i in range(n):
    g = []
    for j in range(n):
      if abs(j-i) < d:
        g.append(j)
    groups.append(g)

  used = [0]*n
  sofar = [0]*n
  def recur(i=0):
    if i >= n:
      yield tuple(sofar)
      return
    for e in groups[i]:
      if used[e]:
        continue
      used[e] = True
      sofar[i] = e
      yield from recur(i+1)
      used[e] = False

  neigh = []
  claws = set()
  for p in recur():
    t = [e//d for e in p]
    if t == qt:
      continue
    o = Vertex(tuple(t),tuple(p))
    if not e.separated(o):
      neigh.append(o)
      claws.add(tuple(t))
  return neigh, claws


# globals
if __name__ == '__main__':
  from sys import argv
  perm_len = int(argv[1])
  pa_distance = int(argv[2])
  eps = float(argv[3]) if len(argv) > 3 else 0.1

  print (f'P({perm_len}, {pa_distance}) with epsilon {eps}')

  ident_neigh, claws = make_ident_neigh()
  r = len(claws)
  print(f'Degree {len(ident_neigh)} and {len(claws)}-claw free')

  mu, U, rho = feasible_constants(r, eps)
  print(f'mu,U,rho are ', mu,U,rho)

  colors = list(make_colors())
  print(f'There are {len(colors)} colors')

  M = find_it()

  print('M is')
  for v in M.values():
    print(' '.join(map(str, v.data)))

  with open(f'pa_{perm_len}_{pa_distance}_haxell.txt', 'w+') as f:
    for v in M.values():
      f.write(' '.join(map(str, v.data)) + '\n')

