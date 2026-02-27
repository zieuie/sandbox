import itertools as it
import math
import os
import random
from collections import Counter
from datetime import datetime
from time import time


def make_colors(n, d):
  sofar = [0]*n
  num_groups = int(math.ceil(n/d))
  rem = [0]*num_groups
  for x in range(num_groups):
    rem[x] = min(n, d*(x+1)) - d*x

  ret = []
  def recur(i):
    if i >= n:
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


class Haxell:
  def __init__(self, perm_len, pa_distance, colors, eps=0.1):

    self.perm_len = perm_len
    self.pa_distance = pa_distance

    self.backup_interval = 60

    # global variable for edge()
    self.dsquared = pa_distance**2

    # get the neighbors of the identity to calculate r-claw
    _, r = self.get_degree_and_r()

    # compute some constants for Haxell's algorithm
    self.mu, self.U, self.rho = self.feasible_constants(r, eps)

    # compute all the colors
    self.colors = colors

    # here be dragons
    self.ident_class = [list(it.chain(*g)) for g in it.product(*[list(it.permutations(list(range(pa_distance*x, min(perm_len, pa_distance*(x+1)))))) for x in range(int(math.ceil(perm_len/pa_distance)))])]


  ### Metric-specific stuff

  def edge(self, a, b):
    # if [e//pa_distance for e in self] == [e//pa_distance for e in other]:
    #   return False
    i = 0
    L = len(a)
    while i < L:
      if (a[i]-b[i])**2 >= self.dsquared:
        return False
      i += 1
    return True

  def get_color(self, v):
    return tuple(e//self.pa_distance for e in v)

  def from_color(self, A):
    num_groups = int(math.ceil(self.perm_len/self.pa_distance))
    for row in self.ident_class:
      c = [0]*num_groups
      ret = [0]*self.perm_len
      for i,e in enumerate(A):
        ret[i] = row[c[e]+self.pa_distance*e]
        c[e] += 1
      yield tuple(ret)

  def get_neighbors(self, v):
    for pot in ident_neigh:
      yield tuple(pot[e] for e in v)

  def get_degree_and_r(self):
    global ident_neigh
    ident_neigh = self.make_ident_neigh()
    r = len(ident_neigh)
    return sum(map(len,ident_neigh)), r


  def make_ident_neigh(self):
    n,d = self.perm_len, self.pa_distance
    q = list(range(n))
    qt = self.get_color(q)

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
      pt = self.get_color(p)
      if pt == qt:
        continue
      if self.edge(p, q):
        neigh.setdefault(tuple(pt),set()).add(tuple(p))
    return neigh


  ### The easy stuff, not algorithmic per se

  def feasible_constants(self, r, eps):
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







  ### Helpers, not immediately part of the algorithm


  def immediately_addable(self, M, W):
    ret = dict()
    for a,vs in W.items():
      for v in vs:
        if not self.blocks(M,a,v):
          ret.setdefault(a,set()).add(v)
    return ret

  def blocks(self, M,av,v):
    for au,u in M.items():
      if au != av and self.edge(u,v):
        return True
    return False




  def find_addable(self, X,Y,x,y,root_color):
    for a in (Y[-1] if len(Y)>1 else [root_color]):
      for v in self.from_color(a):
        if self.is_addable(X,Y,x,y,a,v):
          return a,v


  def is_addable(self, X,Y,x,y,a,v):
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
    if len(x.get(a,tuple())) >= self.U:
      return False

    # no uv with u in y
    for au,u in y.items():
      if a != au and self.edge(u,v):
        return False

    # no uv with u in x
    for au,us in x.items():
      if au == a:
        continue
      for u in us:
        if self.edge(u,v):
          return False

    # no uv with u in Y<=l
    for y in Y:
      for au,u in y.items():
        if a != au and self.edge(u,v):
          return False

    # no uv with u in X<=l
    for x in X:
      for au,us in x.items():
        for u in us:
          if a != au and self.edge(u,v):
            return False

    # then it's addable
    return True


  ### The Algorithm Proper

  # assumption: l = len(X) = len(Y)
  def build_layer(self, M, X, Y, x, y, root_color):
    if x:
      x = {k:set(v) for k,v in x.items()}
    if y:
      y = {**y}

    while True:
      # this definitely needs some optimizing
      pot = self.find_addable(X,Y,x,y,root_color)
      if pot is None:
        break
      av,v = pot

      x.setdefault(av,set()).add(v)
      for au,u in M.items():
        if au != av and self.edge(u,v):
          y[au] = u

    return x,y

  def grow_transversal(self, M, A):
    ret = dict()
    X = [dict()]
    Y = [dict()]
    l = 0
    while A not in M:
      xpot, ypot = self.build_layer(M,X,Y,dict(),dict(),A)
      X.append(xpot)
      Y.append(ypot)
      if sum(len(e) for e in xpot.values()) <= self.rho*sum(len(e) for e in Y[:l+1]):
        return None

      # collapsing operations
      l += 1
      while True:
        I = self.immediately_addable(M, X[l])
        I_len = sum(len(e) for e in I.values())
        Xl_len = sum(len(e) for e in X[l].values())
        if I_len <= self.mu * Xl_len:
          break

        # augment one row
        if l == 1:
          for a,us in I.items():
            for u in us:
              ret[a] = u
              M[a] = u
              return ret

        # switch around Y[l-1] vertices
        for aw in tuple(Y[l-1].keys()):
          if aw not in I:
            continue
          for u in I[aw]:
            ret[aw] = u
            M[aw] = u
            del Y[l-1][aw]
            break

          # do we really need to recompute I every time?
          I = self.immediately_addable(M, X[l])

        X = X[:l]
        Y = Y[:l]
        l -= 1

        # superposed_build - make layers bigger if possible
        i = 1
        while i <= l:
          xpot, ypot = self.build_layer(M,X[:i],Y[:i],X[i],Y[i],A)
          x_prime_len = sum(len(e) for e in xpot.values())
          x_len = sum(len(e) for e in X[i].values())
          if x_prime_len >= (1 + self.mu) * x_len:
            X[i] = xpot
            Y[i] = ypot
            l = i
          i += 1


