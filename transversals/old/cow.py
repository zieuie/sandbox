import itertools as it
import math
import os
from collections import Counter
from datetime import datetime
from time import time
import random


### The easy stuff, not algorithmic per se

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





### Helpers, not immediately part of the algorithm

VERBOSE = False
def edge(self, other):
  # if [e//pa_distance for e in self] == [e//pa_distance for e in other]:
  #   return False
  i = 0
  L = len(self)
  while i < L:
    if (self[i]-other[i])**2 >= dsquared:
      return False
    i += 1
  return True

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




def find_addable(X,Y,x,y,root_color):
  for a in (Y[-1] if len(Y)>1 else [root_color]):
    for v in from_color(a):
      if is_addable(X,Y,x,y,a,v):
        return a,v


def is_addable(X,Y,x,y,a,v):
  # v not in Y_l
  if a in Y[-1] and v == Y[-1][a]:
    return False

  # v not in x
  if a in x and v in x[a]:
    return False

  # v not in y
  if a in y and v == y[a]:
    return False
  
  # |A(v) ^ X| < U
  if len(x.get(a,tuple())) >= U:
    return False

  # no uv with u in y
  for au,u in y.items():
    if a != au and edge(u,v):
      return False

  # no uv with u in x
  for au,us in x.items():
    if au == a:
      continue
    for u in us:
      if edge(u,v):
        return False

  # no uv with u in Y<=l
  for y in Y:
    for au,u in y.items():
      if a != au and edge(u,v):
        return False

  # no uv with u in X<=l
  for x in X:
    for au,us in x.items():
      for u in us:
        if a != au and edge(u,v):
          return False

  # then it's addable
  return True


### The Algorithm Proper

def grow_transversal(M, A):
  X = [dict()]
  Y = [dict()]
  # A(Y_0) = A
  l = 0
  while A not in M:
    xpot, ypot = build_layer(X,Y,dict(),dict(),A)
    X.append(xpot)
    Y.append(ypot)
    if sum(len(e) for e in xpot.values()) <= rho*sum(len(e) for e in Y[:l+1]):
      # Yle = sum(len(layer) for layer in Y[:l+1])
      # Xnext = sum(len(vs) for vs in xpot.values())
      # VERBOSE and print(f"|X_{l+1}|={Xnext}  |Y_≤{l}|={Yle}  rho*|Y_≤l|={rho*Yle}")
      # VERBOSE and print("Y layer sizes:", [len(layer) for layer in Y])
      # VERBOSE and print("X layer sizes:", [sum(len(vs) for vs in layer.values()) for layer in X])      
      return X, Y, xpot, ypot, l

    # collapsing operations
    l += 1
    while True:
      I = immediately_addable(M, X[l])
      I_len = sum(len(e) for e in I.values())
      Xl_len = sum(len(e) for e in X[l].values())
      if I_len <= mu * Xl_len:
        break

      # augment one row
      if l == 1:
        for a,us in I.items():
          for u in us:
            M[a] = u
            return None

      # switch around Y[l-1] vertices
      for aw in tuple(Y[l-1].keys()):
        if aw not in I:
          continue
        for u in I[aw]:
          M[aw] = u
          del Y[l-1][aw]
          break
        
        # do we really need to recompute I every time?
        I = immediately_addable(M, X[l])


      X = X[:l]
      Y = Y[:l]
      l -= 1

      # superposed_build - make layers bigger if possible
      i = 1
      while i <= l:
        xpot, ypot = build_layer(X[:i],Y[:i],X[i],Y[i],A)
        x_prime_len = sum(len(e) for e in xpot.values())
        x_len = sum(len(e) for e in X[i].values())
        if x_prime_len >= (1 + mu) * x_len:
          X[i] = xpot
          Y[i] = ypot
          l = i
        i += 1


# assumption: l = len(X) = len(Y)
def build_layer(X, Y, x, y, root_color):
  if x:
    x = {k:set(v) for k,v in x.items()}
  if y:
    y = {**y}

  # active classes we try to grow from
  # frontier = list(Y[-1].keys()) if len(Y) > 1 else [root_color]
  # VERBOSE and print("\n=== build_layer ===")
  # VERBOSE and print("l =", len(X)-1, "frontier size =", len(frontier))
  # VERBOSE and print("frontier sample:", frontier[:5])
  # VERBOSE and print("|M| =", len(M), "root_color =", root_color)
  # VERBOSE and print("current x sizes:", {a: len(vs) for a,vs in x.items()})
  # VERBOSE and print("current y size:", len(y))

  while True:
    # this definitely needs some optimizing
    pot = find_addable(X,Y,x,y,root_color)
    if pot is None:
      break
    av,v = pot

    x.setdefault(av,set()).add(v)
    for au,u in M.items():
      if au != av and edge(u,v):
        y[au] = u

  return x,y


def find_it(M, colors):
  # random.shuffle(colors)
  for A in colors:
    print(datetime.now(), len(M), len(colors))
    pot = grow_transversal(M, A)
    if pot is not None:
      print('Failed', len(M), A)
      X, Y, xfail, yfail, l = pot
      B,D = make_BD(M, A, X, Y, xfail, yfail, l)
      return B,D

  print('Success', len(M))
  return None



### The BD part

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

  # here be dragons
  ident_class = [list(it.chain(*g)) for g in it.product(*[list(it.permutations(list(range(pa_distance*x, min(perm_len, pa_distance*(x+1)))))) for x in range(int(math.ceil(perm_len/pa_distance)))])]

  a = tuple(e//pa_distance for e in range(perm_len))
  v = tuple(range(perm_len))
  for u in from_color(a):
    if not edge(u,v):
      print('moo', u,v)
      input()


  # # main driver
  # state = find_it(M, colors)
  # if state is None:
  #   print('Success!')
  #   exit(0)

  # B,D = state

  # print()
  # print('B:')
  # for e in B:
  #   print('  -', e)
  # print()
  # print('D:')
  # for k,vs in D.items():
  #   print()
  #   print(k)
  #   for e in vs:
  #     print('  -', e)
  # print()

  # good = True    
  # for a in B:
  #   for v in from_color(a):
  #     if not is_dominated(a,v,D):
  #       print('not dominated:', v)
  #       good = False
  # print('Proper BD:', good)
  # print(len(B), sum(len(e) for e in D.values()))


