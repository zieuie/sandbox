import itertools as it
import math
import os
from collections import Counter
from datetime import datetime
from time import time

VERBOSE = False


def edge(self, other):
  i = 0
  L = len(self)
  while i < L:
    if (self[i]-other[i])**2 >= dsquared:
      return False
    i += 1
  return True


def find_it(M=None):
  global Ay0

  M = M or dict()
  for _ in range(len(M), len(colors)):
    for A in colors:
      if A not in M:
        break
    else:
      break

    # if not i % 100:
    # print(datetime.now(), f'find_it iteration {i} of {len(colors)}')

    Ay0 = A
    if grow_transversal(M, A):
      yield M
    else:
      yield None
      break


def grow_transversal(M, A):
  # Xs = [{ color: {vertices} }, ...]
  Xs = [dict()]
  # Ys = [{ color: vertex }, ...]
  Ys = [dict()]
  l = 0
  VERBOSE and print('|',end='',flush=True)
  while A not in M:
    VERBOSE and print('*',end='',flush=True)
    forbidden = calc_forbidden(Xs,Ys,dict(),dict(),l)
    colors = (Ys[l] if l else [Ay0])
    X,Y = build_layer(M,dict(),dict(),forbidden,colors)

    Yllen = sum(map(len,Ys))
    if sum(map(len,X.values())) <= rho * Yllen:
      return None

    Xs.append(X)
    Ys.append(Y)
    l += 1
    while True:
      VERBOSE and print('^',end='',flush=True)
      # Icount : int
      # Imap[color] = vertex
      Icount, Iany, Iany_color = immediate_count(M,Xs[l],l)
      if Icount <= mu * sum(map(len,Xs[l].values())):
        break

      if l == 1:
        M[Iany_color] = Iany
        return M

      to_delete = set()
      for w_color in Ys[l-1]:
        if w_color not in Xs[l]:
          continue

        _, u, _ = immediate_count(M, {w_color: Xs[l][w_color]},l)
        if u is None:
          continue

        M[w_color] = u
        to_delete.add(w_color)

      for a in to_delete:
        Ys[l-1].pop(a)

      # superposed_build
      Xs, Ys, l = Xs[:l], Ys[:l], l-1
      for l in range(1,l+1):
        forbidden = calc_forbidden(Xs,Ys,Xs[l],Ys[l],l-1)
        colors = (Ys[l-1] if l-1 else [Ay0])
        X,Y = build_layer(M,Xs[l],Ys[l],forbidden,colors)
        if sum(map(len,X.values())) >= (1+mu)*sum(map(len,Xs[l].values())):
          Xs[l], Ys[l] = X,Y
          break
      Xs, Ys = Xs[:l+1], Ys[:l+1]


def calc_forbidden(Xs,Ys,X,Y,l):
  forbidden = set()
  forbidden.update(Y.values(), *X.values())
  for i in range(l+1):
    forbidden.update(Ys[i].values(), *Xs[i].values())
  return forbidden


def build_layer(M,X,Y,forbidden,colors):
  VERBOSE and print('(',end='',flush=True)
  if X:
    X = {k:set(v) for k,v in X.items()}
  if Y:
    Y = {**Y}

  for A in colors:
    VERBOSE and print('A',end='',flush=True)
    for v in from_color(A):
      if len(X.get(A, tuple())) >= U:
        break

      if v in forbidden:
        continue

      good = True
      for u in forbidden:
        if edge(u,v) and tuple(e//pa_distance for e in u) != A:
          good = False
          break
      if not good:
        continue

      # X = X u {v}
      X.setdefault(A, set()).add(v)
      forbidden.add(v)

      # Y = Y u {u in M | uv in E}
      for u_color, u in M.items():
        if A != u_color and edge(v, u):
          Y[u_color] = u
          forbidden.add(u)

  VERBOSE and print(')',end='',flush=True)
  return X,Y


def immediate_count(M,W,l):
  Icount = 0
  Iany = None
  Iany_color = None

  for v_color, vs in W.items():
    for v in vs:
      for u_color, u in M.items():
        if v_color != u_color and edge(u, v):
          break
      else:
        if l != 1 or v_color == Ay0:
          Icount += 1
          Iany = v
          Iany_color = v_color

  return Icount, Iany, Iany_color


# @functools.lru_cache(maxsize=2)
def from_color(A):
  global cgroups
  ret = []
  for g in it.product(*cgroups):
    g = list(map(iter, g))
    ret.append(tuple(next(g[e]) for e in A))
  return ret


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

  neigh = dict()
  for p in recur():
    pt = [e//d for e in p]
    if pt == qt:
      continue
    if edge(p, q):
      neigh.setdefault(tuple(pt),set()).add(tuple(p))
  return neigh


def make_foes():
  n,d = perm_len, pa_distance
  q = tuple(range(n))
  qt = tuple(e//d for e in q)






# globals
if __name__ == '__main__':
  from sys import argv
  perm_len = int(argv[1])
  pa_distance = int(argv[2])
  eps = float(argv[3]) if len(argv) > 3 else 0.1
  dsquared = pa_distance**2

  print (f'P({perm_len}, {pa_distance}) with epsilon {eps}')

  ident_neigh = make_ident_neigh()
  r = len(ident_neigh)
  print(f'Degree {sum(map(len,ident_neigh))} and {r}-claw free')
  print(sorted(Counter(map(len,ident_neigh.values())).items()))

  mu, U, rho = feasible_constants(r, eps)
  print(f'mu,U,rho are ', mu,U,rho)

  colors = list(make_colors())
  print(f'There are {len(colors)} colors')

  # globals for from_color()
  cgroups = []
  for x in range(int(math.ceil(perm_len/pa_distance))):
    cgroups.append(list(range(pa_distance*x, min(perm_len, pa_distance*(x+1)))))
  cgroups = list(map(list,map(it.permutations, cgroups)))


  # import cProfile

  # pr = cProfile.Profile()
  # pr.enable()  # Start profiling

  good = False
  M = dict()
  filename = f'pa_{perm_len}_{pa_distance}_haxell.txt'
  if os.path.exists(filename):
    print(f'Found a file named {filename}')
    with open(filename, 'r') as f:
      good = True
      for line in f:
        line = line.split('#')[0].strip()
        row = tuple(map(int, line.split()))
        color = tuple(e//pa_distance for e in row)
        M[color] = row
        if len(row) != perm_len:
          print(f'One row has {len(row)} permutations. We are not using this file.')
          if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
            exit(0)

          good = False
          break
        
      for _,u in M.items():
        for _,v in M.items():
          if u != v and edge(u,v):
            print('Not separated 1:', u)
            print('Not separated 2:', v)
            print(f'Two rows are not separated. We are not using this file.')
            if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
              exit(0)

            good = False
            break
        if not good:
          break

  if not good:
    M = dict()

  t = time()
  for M in find_it(M):
    if M is None:
      print('Failure!')
      exit(0)
    
    i = len(M)
    print(datetime.now(), f'iteration {i} of {len(colors)}')
    if i % 100 == 0 or time() - t > 10:
      print(datetime.now(), f'Writing {len(M)} permutations to {filename}')
      with open(filename, 'w+') as f:
        for row in M.values():
          f.write(' '.join(map(str, row)) + '\n')
      t = time()

  # pr.disable()  # Stop profiling
  # pr.print_stats(sort='tottime') # Print statistics, sorted by cumulative time
  # exit(0)


  # M = find_it()

  print('M is')
  for v in M.values():
    print(' '.join(map(str, v)))

  with open(f'pa_{perm_len}_{pa_distance}_haxell.txt', 'w+') as f:
    for v in M.values():
      f.write(' '.join(map(str, v)) + '\n')

