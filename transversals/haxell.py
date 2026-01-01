import itertools as it
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from datetime import datetime


VERBOSE = False
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
  for i,_ in enumerate(colors):
    for A in colors:
      if A not in M:
        break
    else:
      raise ValueError('Could not find a class not in M')

    VERBOSE and print()
    VERBOSE and print()
    VERBOSE and print('-'*80)
    print(datetime.now(), f'find_it iteration {i+1} of {len(colors)}')

    Ay0 = A
    M = grow_transversal(M, A)
    if M is None:
      return None
  return M


def grow_transversal(M, A):
  M = deepcopy(M)
  VERBOSE and print('grow_transversal, A =', A)

  # Xs = [{ color: {vertices} }, ...]
  Xs = [dict()]
  # Ys = [{ color: vertex }, ...]
  Ys = [dict()]
  l = 0
  while A not in M:
    VERBOSE and print('while A not in M', l)
    X,Y = build_layer2(M,Xs,Ys,dict(),dict(),l)
    Xs.append(X)
    Ys.append(Y)


    Yllen = sum(map(len,Ys))
    if sum(map(len,X.values())) <= rho * Yllen:
      return None

    l += 1
    while True:
      VERBOSE and print('M is:')
      for v in M.values():
        VERBOSE and print('  ', v)
      VERBOSE and print(len(M))
      VERBOSE and print()

      VERBOSE and print('T is:')
      for i in range(1,l+1):
        VERBOSE and print(f'..[layer {i}]')
        for k in set(Ys[i].keys()) | set(Xs[i].keys()):
          y,x = Ys[i].get(k), Xs[i].get(k)
          # VERBOSE and print('  ', k, '|', y, '|', ' '.join(map(str, x)) if x else 'None')
          VERBOSE and print('  ', k, '|', y)
      VERBOSE and print(len(Ys), len(Xs), l)
      VERBOSE and print()

      # Icount : int
      # Imap[color] = vertex
      Icount, Iany = immediate_count(M,Xs[l])
      if Icount <= mu * len(Xs[l]):
        break

      if l == 1:
        VERBOSE and print('l=1, adding', Iany)
        M[Iany.color] = Iany
        return M

      to_delete = set()
      for w in Ys[l-1].values():
        if w.color not in Xs[l]:
          continue

        _, u = immediate_count(M, {w.color: Xs[l][w.color]})
        if u is None:
          continue

        VERBOSE and print('l!=1, replacing', w, 'with', u)
        M[w.color] = u
        # del Ys[l-1][w.color]
        to_delete.add(w.color)
      
      for a in to_delete:
        Ys[l-1].pop(a)

      Xs, Ys, l = superposed_build(M, Xs[:l], Ys[:l], l-1)


def superposed_build(M, Xs, Ys, l):
  VERBOSE and print('superposed_build', l)
  i = 1
  while i <= l:
    VERBOSE and print('superposed_build iteration', i)
    VERBOSE and print('M is:')
    for v in M.values():
      VERBOSE and print('  ', v)
    VERBOSE and print(len(M))
    VERBOSE and print()

    VERBOSE and print('T is:')
    for j in range(1,l+1):
      VERBOSE and print(f'..[layer {j}]')
      for k in set(Ys[j].keys()) | set(Xs[j].keys()):
        y,x = Ys[j].get(k), Xs[j].get(k)
        VERBOSE and print('  ', k, '|', y)
    VERBOSE and print(len(Ys), len(Xs), l)
    VERBOSE and print()

    X, Y = build_layer2(M,Xs,Ys,Xs[i],Ys[i],l)
    if sum(map(len,X.values())) >= (1+mu)*len(Xs[i]):
      Xs[i], Ys[i] = X,Y
      l = i
    i += 1
  return Xs[:l+1], Ys[:l+1], l


def build_layer(M,Xs,Ys,X,Y,l):
  X = deepcopy(X)
  Y = deepcopy(Y)
  for i in it.count():
    VERBOSE and print('.', end='', flush=True)
    # print('.', end='', flush=True)
    # while v in A(Y_l) is addable for X,Y,T..
    v = addable(M,Xs,Ys,X,Y,l)
    if v is None:
      break

    # X = X u {v}
    X.setdefault(v.color, set()).add(v)

    # Y = Y u {u in M | uv in E}
    for u in M.values():
      if v.edge(u):
        if u.color in Y and u != Y[u.color]:
          VERBOSE and print('uh oh', Y, u)
          input()
        Y[u.color] = u

  return X,Y


def addable(M, Xs, Ys, X, Y, l):
  global Ay0
  # M[class] = vertex
  # Xs[layer_no] = { color: vertex }
  # Ys[layer_no] = { color: {vertices} }

  for A in (Ys[l] if l else [Ay0]):
    if len(X.get(A, tuple())) >= U:
      continue
    for v in from_color(A):
      # we don't have it
      if A in Ys[l] and v == Ys[l][A]:
        continue
      if A in X and v in X[A]:
        continue
      if A in Y and v == Y[A]:
        continue

      # we don't have its neighbors
      good = True
      for u in v.neighbors():
        if u.color in X and u in X[u.color]:
          good = False
          break
        if u.color in Y and u == Y[u.color]:
          good = False
          break
        for i in range(l+1):
          if u.color in Xs[i] and u in Xs[i][u.color]:
            good = False
            break
          if u.color in Ys[i] and u == Ys[i][u.color]:
            good = False
            break
        if not good:
          break

      if good:
        return v


def build_layer2(M,Xs,Ys,X,Y,l):
  global Ay0
  X = deepcopy(X)
  Y = deepcopy(Y)
  VERBOSE and print('build_layer', l)

  for A in (Ys[l] if l else [Ay0]):
    if len(X.get(A, tuple())) >= U:
      continue
    for v in from_color(A):
      if not is_good(A,v,Xs,Ys,X,Y,l):
        continue

      VERBOSE and print('.', end='', flush=True)

      # X = X u {v}
      X.setdefault(v.color, set()).add(v)

      # Y = Y u {u in M | uv in E}
      for u in M.values():
        if v.edge(u):
          if u.color in Y:
            VERBOSE and print('uh oh', Y, u, v, u in set(v.neighbors()))
            input()
          Y[u.color] = u

  return X,Y


def is_good(A, v, Xs, Ys, X, Y, l):
  if A != v.color:
    VERBOSE and print('oh shoot', A, v)
    input()

  # if A in Ys[l] and v == Ys[l][A]:
  #   return False
  for i in range(l+1):
    if A in Ys[i] and v == Ys[i][A]:
      return False

  if A in X and v in X[A]:
    return False

  if A in Y and v == Y[A]:
    return False

  # we don't have its neighbors
  for u in v.neighbors():

    if u.color in X and u in X[u.color]:
      return False

    if u.color in Y and u == Y[u.color]:
      return False

    for i in range(l+1):
      if u.color in Xs[i] and u in Xs[i][u.color]:
        return False
      if u.color in Ys[i] and u == Ys[i][u.color]:
        return False

  return True


def immediate_count(M,W):
  Icount = 0
  Iany = None

  # the neighborly way
  # for vs in W.values():
  #   for v in vs:
  #     for u in v.neighbors():
  #       if u.color in M and u == M[u.color]:
  #         break
  #     else:
  #       Icount += 1
  #       Iany = v

  for v in it.chain(*W.values()):
    for u in M.values():
      if u.edge(v):
        break
    else:
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

  neigh = []
  claws = set()

  for p in it.permutations(q):
    t = [e//d for e in p]
    if t == qt:
      continue
    o = Vertex(tuple(t),tuple(p))
    if not e.separated(o):
      neigh.append(o)
      claws.add(tuple(t))
  return neigh, claws

# globals
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


# p = [0, 1, 2, 3, 4, 7, 6, 5, 8]
# v = Vertex(tuple([e//3 for e in p]), tuple(p))
# for x in v.neighbors():
#   VERBOSE and print(x, x.separated(v))
#   if x.data == (2, 1, 0, 5, 4, 8, 7, 3, 6):
#     VERBOSE and print('here')
#     input()

# uu = (2, 1, 0, 5, 4, 8, 7, 3, 6)
# u = Vertex(tuple([e//3 for e in uu]), tuple(uu))
# VERBOSE and print (u.separated(v), v.separated(u))
# for x in v.neighbors():
#   VERBOSE and print(x, x.separated(v))
#   if x.data == uu:
#     VERBOSE and print('here')
#     input()

# VERBOSE and print(u, u.separated(v), v.data in set(e.data for e in u.neighbors()))
# VERBOSE and print(v, v.separated(u), u.data in set(e.data for e in v.neighbors()))


# n,d = perm_len, pa_distance
# q = [0, 1, 2, 3, 4, 7, 6, 5, 8]
# qt = [e//d for e in q]
# v = Vertex(tuple(qt),tuple(q))
# for p in it.permutations(q):
#   t = [e//d for e in p]
#   if t == qt:
#     continue
#   o = Vertex(tuple(t),tuple(p))
#   if p == tuple(uu):
#     VERBOSE and print('moo')
  # if not v.separated(o) and o.data == tuple(uu):
    # VERBOSE and print(o.data)