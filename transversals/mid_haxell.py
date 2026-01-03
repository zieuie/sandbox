import itertools as it
import math
import os
from collections import Counter
from datetime import datetime
from time import time


def edge(self, other):
  i = 0
  L = len(self)
  while i < L:
    if (self[i]-other[i])**2 >= dsquared:
      return False
    i += 1
  return True


def find_it(M, colors):
  t = time()
  for _ in range(len(M), len(colors)):
    # pick an unused color
    for A in colors:
      if A not in M:
        break
    else:
      break

    if not grow_transversal(M, A):
      return False

    # print some status
    i = len(M)
    print(datetime.now(), f'P({perm_len},{pa_distance}) >= {i} of {len(colors)}')

    # do a backup maybe
    if time() - t > backup_interval:
      print(datetime.now(), f'Writing {i} permutations to {filename}')
      with open(filename, 'w+') as f:
        for row in M.values():
          f.write(' '.join(map(str, row)) + '\n')
      t = time()

  # we terminated! write it to a final file
  print(datetime.now(), f'Writing {len(M)} permutations to {filename}')
  with open(filename, 'w+') as f:
    for v in M.values():
      f.write(' '.join(map(str, v)) + '\n')
  return True


def grow_transversal(M, A):
  # Xs = [{ color: {vertices} }, ...]
  Xs = [dict()]
  # Ys = [{ color: vertex }, ...]
  Ys = [dict()]
  l = 0
  while A not in M:
    forbidden = calc_forbidden(Xs,Ys,dict(),dict(),l)
    a_list = (Ys[l] if l else [A])
    X,Y = build_layer(M,dict(),dict(),forbidden,a_list)

    Yllen = sum(map(len,Ys))
    if sum(map(len,X.values())) <= rho * Yllen:
      return None

    Xs.append(X)
    Ys.append(Y)
    l += 1
    while True:
      # Icount : int
      # Imap[color] = vertex
      Icount, Iany, Iany_color = immediate_count(M,Xs[l],l, A)
      if Icount <= mu * sum(map(len,Xs[l].values())):
        break

      if l == 1:
        M[Iany_color] = Iany
        return M

      to_delete = set()
      for w_color in Ys[l-1]:
        if w_color not in Xs[l]:
          continue

        _, u, _ = immediate_count(M, {w_color: Xs[l][w_color]},l, A)
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
        a_list = (Ys[l-1] if l-1 else [A])
        X,Y = build_layer(M,Xs[l],Ys[l],forbidden,a_list)
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


def build_layer(M,X,Y,forbidden,a_list):
  if X:
    X = {k:set(v) for k,v in X.items()}
  if Y:
    Y = {**Y}

  for A in a_list:
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

  return X,Y


def immediate_count(M,W,l,A):
  Icount = 0
  Iany = None
  Iany_color = None

  for v_color, vs in W.items():
    for v in vs:
      for u_color, u in M.items():
        if v_color != u_color and edge(u, v):
          break
      else:
        if l != 1 or v_color == A:
          Icount += 1
          Iany = v
          Iany_color = v_color

  return Icount, Iany, Iany_color


def from_color(A):
  num_groups = int(math.ceil(perm_len/pa_distance))
  for row in ident_class:
    c = [0]*num_groups
    ret = [0]*perm_len
    for i,e in enumerate(A):
      ret[i] = row[c[e]+pa_distance*e]
      c[e] += 1
    yield tuple(ret)


def make_colors():
  sofar = [0]*perm_len
  num_groups = int(math.ceil(perm_len/pa_distance))
  rem = [0]*num_groups
  for x in range(num_groups):
    rem[x] = min(perm_len, pa_distance*(x+1)) - pa_distance*x

  ret = []
  def recur(i):
    if i >= perm_len:
      ret.append(tuple(sofar))
      return

    for k in range(len(rem)):
      if rem[k]:
        rem[k] -= 1
        sofar[i] = k
        recur(i+1)
        rem[k] += 1

  recur(0)
  return ret


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


def resume_computation(filename):
  good = False
  M = dict()
  if os.path.exists(filename):
    print(f'Found a file named {filename}')
    with open(filename, 'r') as f:
      good = True
      for line in f:
        # clean up the line
        line = line.split('#')[0].strip()
        if len(line) == 0:
          continue

        # get the permutation out of it
        row = tuple(map(int, line.split()))
        color = tuple(e//pa_distance for e in row)
        M[color] = row

        # validate the permutation
        if len(row) != perm_len:
          good = False
          print(f'One row has {len(row)} permutations. We are not using this file.')
          if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
            exit(0)

      # verify the partitial independent transversal
      P = list(M.values())
      for ux,u in enumerate(P):
        for vx in range(ux):
          v = P[vx]
          if edge(u,v):
            good = False
            print('Not separated 1:', u)
            print('Not separated 2:', v)
            print(f'Two rows are not separated. We are not using this file.')
            if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
              exit(0)

  if good:
    return M
  return dict()


# globals
backup_interval = 60
HELP_STR = f'''
haxell.py

Creates (n choose d) permutation arrays using Haxell's algorithm
for independent transversals. A backup will be made every {backup_interval} seconds.

This program can resume from any partial permutation array stored in
a file named pa_n_d_haxell.txt. For example, pa_12_3_haxell.txt,
if it exists, will be used on startup.

Usage:
  pypy3 haxell.py N D [epsilon]

Where:
  N       - Permutation length
  D       - Chebyshev distance
  epsilon - A parameter for Haxell's algorithm (Default 0.1)
'''

if __name__ == '__main__':
  from sys import argv
  # parameters are globally used
  try:
    perm_len = int(argv[1])
    pa_distance = int(argv[2])
    eps = float(argv[3]) if len(argv) > 3 else 0.1
  except:
    print(HELP_STR)
    exit(1)

  # global variable for edge()
  dsquared = pa_distance**2

  # read a file if it exists
  print (f'P({perm_len}, {pa_distance}) with epsilon {eps}')
  filename = f'pa_{perm_len}_{pa_distance}_haxell.txt'
  # M = resume_computation(filename)
  M = dict()

  # get the neighbors of the identity to calculate r-claw
  ident_neigh = make_ident_neigh()
  r = len(ident_neigh)
  print(f'Degree {sum(map(len,ident_neigh))} and {r}-claw free')
  print(sorted(Counter(map(len,ident_neigh.values())).items()))

  # compute some constants for Haxell's algorithm
  mu, U, rho = feasible_constants(r, eps)
  print(f'mu,U,rho are ', mu,U,rho)

  # compute all the colors
  colors = list(make_colors())
  print(f'There are {len(colors)} colors')

  # here be dragons
  ident_class = [list(it.chain(*g)) for g in it.product(*[list(it.permutations(list(range(pa_distance*x, min(perm_len, pa_distance*(x+1)))))) for x in range(int(math.ceil(perm_len/pa_distance)))])]
  
  # main driver
  t = time()
  if not find_it(M, colors):
    print('Haxell halted without an independent transversal!')
    exit(1)

  print('Success!')
