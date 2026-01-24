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
    state = grow_transversal(M, A)

    if state is None:
      yield M
      continue

    X, Y, xfail, yfail, l = state
    B,D = make_BD(M, A, X, Y, xfail, yfail, l)
    
    print()
    print('B:')
    for e in B:
      print('  -', e)
    print()
    print('D:')
    for k,vs in D.items():
      print()
      print(k)
      for e in vs:
        print('  -', e)
    print()

    good = True    
    for a in B:
      for v in from_color(a):
        if not is_dominated(a,v,D):
          print('not dominated:', v)
          good = False
    print('Proper BD:', good)
    print(len(B), sum(len(e) for e in D.values()))
    exit(1)


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
      return Xs, Ys, X, Y, l

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
        return None

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

      # verify the partial independent transversal
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










### The BD part


def immediately_addable(M, W):
  ret = dict()
  for a,vs in W.items():
    for v in vs:
      if not blocks(M,a,v):
        ret.setdefault(a,set()).add(v)
  return ret

def blocks(M,av,v):
  for au,u in M.items():
    if au != av and edge(u,v):
      return True
  return False

def compute_B(A, Y, l):
    """
    A is root class.
    Y is list of dicts: Y[i][class] = blocking vertex (from M)
    l is current depth at failure (we tried to build layer l+1 and failed)
    """
    B = {A}
    # Y_0 is empty in your code; paper treats A(Y_0)=A specially.
    for i in range(1, l+1):
        B.update(Y[i].keys())
    return B

def remove_AU_from_B(B, A, X, U):
    # A^U = "the first U vertices in class A" in the paper’s ordering.
    # In your code, simplest proxy: if |X_1[A]| reached U, drop A from B.
    if 1 < len(X) and len(X[1].get(A, ())) >= U:
        B.discard(A)
    return B

def compute_D(X, Y, xfail=None, yfail=None):
    D = {}
    # layers 1..l (skip layer 0 placeholder)
    for i in range(1, len(X)):
        for a, vs in X[i].items():
            D.setdefault(a, set()).update(vs)
    for i in range(1, len(Y)):
        for a, v in Y[i].items():
            D.setdefault(a, set()).add(v)
    if xfail:
        for a, vs in xfail.items():
            D.setdefault(a, set()).update(vs)
    if yfail:
        for a, v in yfail.items():
            D.setdefault(a, set()).add(v)
    return D

def compute_S(M, W):
    """
    M: transversal dict[class]->vertex
    W: dict[class]->iterable of vertices (candidate pool)
    S: dict[class(u)] -> set(vertices u)
    where u is the minimum neighbor of v among v in Im(W).
    """
    S = {}
    for v in immediately_addable(M, W):
        best_u = None
        for pot in ident_neigh:
            u = tuple(pot[e] for e in v)
            if best_u is None or u < best_u:
                best_u = u
        if best_u is not None:
            au = tuple(e//pa_distance for e in best_u)
            S.setdefault(au, set()).add(best_u)
    return S

def make_BD(M, A, X, Y, xfail, yfail, l):
    B = compute_B(A, Y, l)
    B = remove_AU_from_B(B, A, X, U)

    D = compute_D(X, Y, xfail, yfail)

    # Optionally restrict W to classes in B to keep S small:
    W = {a: vs for a, vs in D.items() if a in B}

    S = compute_S(M, W)
    for a, vs in S.items():
        D.setdefault(a, set()).update(vs)

    return B, D

def is_dominated(a,v,D):
  # if a in D and v in D[a]:
  #   return True

  for au,us in D.items():
    for u in us:
      if a != au and edge(u,v):
        return True
      
  return False


















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

  # globals for from_color()
  cgroups = []
  for x in range(int(math.ceil(perm_len/pa_distance))):
    cgroups.append(list(range(pa_distance*x, min(perm_len, pa_distance*(x+1)))))
  cgroups = list(map(list,map(it.permutations, cgroups)))

  # main driver
  t = time()
  for M in find_it(M):
    # get mad
    if M is None:
      print('Haxell halted without an independent transversal!')
      exit(0)
    
    # print some status
    i = len(M)
    print(datetime.now(), f'P({perm_len},{pa_distance}) >= {i} of {len(colors)}')

    # do a backup maybe
    if i % 100 == 0 or time() - t > backup_interval:
      print(datetime.now(), f'Writing {i} permutations to {filename}')
      with open(filename, 'w+') as f:
        for row in M.values():
          f.write(' '.join(map(str, row)) + '\n')
      t = time()

  # we terminated! write it to a final file
  i = len(M)
  print(datetime.now(), f'Writing {i} permutations to {filename}')
  with open(filename, 'w+') as f:
    for v in M.values():
      f.write(' '.join(map(str, v)) + '\n')

  print('Success!')
