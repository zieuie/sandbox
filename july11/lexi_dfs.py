
from lib import *
from collections import *
import itertools as it
import functools
from hill import hill_climb_driver, verify2, separated2
import copy

def make_array(n,d):
  A = []
  for ps in it.combinations(list(range(n)), d):
    t = []
    l, h = 0,n//2
    for x in range(n):
      if x in ps:
        t.append(l)
        l += 1
      else:
        t.append(h)
        h += 1
    A.append(t)
  return A


# { y: [x1, x2, ...] }
# where y is the disjoint set key
# x is an index in 
# So that y: Xs means there's some connected component y
# which consists of the rows and indices D
def make_components(A,d):
  lut = DisjointSet(list(range(len(A))))
  for x in range(len(A)):
    for ccid in range(x):
      if not separated(A[x], A[ccid], d):
        lut.union(x,ccid)
  
  components = defaultdict(list)
  for x in range(len(A)):
    ccid = lut.find(x)
    components[ccid].append(x)
  
  return components


# { x: (prefix, suffix), ... }
# x is the index of C
def make_fixes(C,A,n):
  ret = dict()
  for ccid, vs in C.items():
    v0 = A[ccid]
    if len(vs) < 2:
      ret[ccid] = (v0, v0)
    pre, suf = n-1, 0
    for vx in vs:
      v = A[vx]
      i = 0
      for pot in range(pre+1):
        if v[pot] == v0[pot]:
          i = pot
        else:
          break
      pre = i

      i = n-1
      for suf in reversed(range(suf, n)):
        if v[suf] == v0[suf]:
          i = suf
        else:
          break
      suf = i
    pre,suf = (v0[:pre+1], v0[suf:])
    mid = sorted(set(range(n)) - set(pre) - set(suf))
    ret[ccid] = (pre, suf, mid)
  return ret


def do_shift(row, f):
  # print (f, row)
  # return [f.get(e, e) for e in row]
  return [f[e] for e in row]


# create an n-string from src, inserting symbols qs in positions ps
def infill2(src, ps, qs):
  h = 0
  ret = []
  for i in range(len(src)):
    if i in ps:
      ret.append(qs[h])
      h += 1
    else:
      ret.append(src[i])
  return ret


@functools.cache
def can_construct(mid, d):
  m = len(mid)
  ret = []
  for v in load_pa(f'../results/pa_{m}_choose_{m//2}_verified.txt'):
    ret.append(v)
  
  for dst in it.permutations(mid):
    pot = []
    for row in ret:
      pot.append(do_shift(row, dst))
    if verify(pot, d):
      # print('success for', mid)
      return pot
  # print('failure for', mid, len(ret), d)
  return None


def cow(hs, ls, pre, suf, d):
  h,l = 0,0

  nexpre = []
  for e in pre:
    if e < d:
      nexpre.append(ls[l])
      l += 1
    else:
      nexpre.append(hs[h])
      h += 1

  nexsuf = []
  for e in suf:
    if e < d:
      nexsuf.append(ls[l])
      l += 1
    else:
      nexsuf.append(hs[h])
      h += 1

  return nexpre, nexsuf
import random

@functools.cache
def all_goods(hn,ln,n,d):
  ret = []
  lcms = list(it.combinations(list(range(0, d)), ln))
  hcms = list(it.combinations(list(range(d, n)), hn))
  lshs = list(it.product(lcms, hcms))
  # random.shuffle(lshs)
  for ls,hs in lshs:
      mid2 = tuple(sorted(set(range(n)) - set(hs) - set(ls)))
      if not can_construct(mid2, d):
        continue

      lpms = list(it.permutations(ls))
      hpms = list(it.permutations(hs))
      lphp = list(it.product(lpms, hpms))
      # random.shuffle(lphp)
      for ls2, hs2 in lphp:
        ret.append((hs2, ls2, mid2))
  return ret

from time import time
from datetime import datetime

DEEPEST = -1
def fillz(Z,n):
  d = n//2
  # print('fillz', n, d, len(Z))

  sofar = []
  def recur():
    global START_TIME
    global TIMEOUT
    global DEEPEST

    block = len(sofar)

    # print('>'*(block+1), block, 'of', len(Z))
    if block > DEEPEST:
      DEEPEST = block
      # print('>'*(block+1), block, 'of', len(Z))

      for pre, suf, _,_ in sofar:
        m = n - len(pre) - len(suf)
        print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))

      print(datetime.now(), n, d, TIMEOUT, block, 'of', len(Z))

    # if we're done, yield!
    if block >= len(Z):
      yield copy.deepcopy(sofar)
      return

    pre, suf, hn, ln = Z[block]
    gs = all_goods(hn,ln,n,d)
    # print('lengies', len(gs))
    # random.shuffle(gs)
    for hs, ls, mid2 in gs:
      # if time() - START_TIME > max(TIMEOUT, DEEPEST):
      if TIMEOUT and time() - START_TIME > TIMEOUT:
        return
      nexpre, nexsuf = cow(hs, ls, pre, suf, d)
      nex = nexpre + [-1]*len(mid2) + nexsuf
      for t in sofar:
        if not separated2(nex, t[-1], d):
          break
      else:
        sofar.append((nexpre, nexsuf, mid2, nex))
        yield from recur()
        sofar.pop()

  yield from recur()


def anger(Z,d):
  sofar = []
  cofar = []
  used = [False]*len(Z)
  T = []
  for pre,suf,_,_ in Z:
    m = (-1,)*(2*d - len(pre) - len(suf))
    T.append(pre + m + suf)

  def recur():
    if len(sofar) >= len(Z):
      yield
      return

    bestr, bestc = [],float('inf')
    for u in range(len(Z)):
      if used[u]:
        continue
      cov = [0]*(2*d)
      for v in sofar:
        vcount = set()
        for i,(x,y) in enumerate(zip(T[u],T[v])):
          if x >= 0 and y >= 0 and (x<d) != (y<d):
            vcount.add(i)
        cov[len(vcount)] += 1

      val = sum(int(e*100**x) for x,e in enumerate(cov))
      if val < bestc:
        bestr, bestc = [u], val
      elif val == bestc:
        bestr.append(u)
    
    if Q[len(cofar)] < bestc:
      return

    for u in bestr:
      sofar.append(u)
      cofar.append(bestc)
      used[u] = True
      yield from recur()
      used[u] = False
      cofar.pop()
      sofar.pop()

  Q, W = (float('inf'),) * len(Z), []
  for _ in recur():
    # if Q == tuple(cofar):
    #   W.append(copy.deepcopy(sofar))  
    if sorted((Q,tuple(cofar)))[0] == tuple(cofar) and tuple(cofar) != Q:
      Q = tuple(cofar)
      W = copy.deepcopy(sofar)

  print('winner', Q)
  # return Q, W
  return W


def main(n):
  global START_TIME
  global TIMEOUT
  # make connected components
  d = n//2
  A = make_array(n,d)
  C = make_components(A,d)
  F = list(make_fixes(C,A,n).values())

  # sort by size and split isolates from real components
  F = sorted(F, key=lambda t: -len(t[-1]))
  Z = []
  ISOLATES = []
  
  for pre,suf,mid in F:
    if len(mid) == 0:
      ISOLATES.append(pre)
      continue
    hn,ln = 0,0
    for e in it.chain(pre, suf):
      if e >= d:
        hn += 1
      else:
        ln += 1
    Z.append((tuple(pre),tuple(suf),hn,ln))

  for pre, suf, hn, ln in Z:
    m = n - len(pre) - len(suf)
    print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))

  # Y = Z
  # Z = [Y[e] for e in anger(Y,d)]
  # print('Z', len(Z))

  print()
  for pre, suf, _,_ in Z:
    random.shuffle(Z)
    m = n - len(pre) - len(suf)
    print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))


  while True:
    random.shuffle(Z)
    print('! Z', [n - len (pre) -len(suf) for pre,suf,hn,ln in Z])
    START_TIME = time()
    for pot in fillz(Z,n):

      print()
      for pre, suf, _,_ in pot:
        m = n - len(pre) - len(suf)
        print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))

      ret = []
      for pre, suf, mid2, _ in pot:
        print()
        for row in can_construct(mid2, d):
          nex = pre + row + suf
          print(nex)
          ret.append(nex)
      
      if verify(ret, d):
        dump_pa(ret, f'cc_graph_pa_{n}_choose_{d}_verified.txt', verbose=True)
        dump_pa(ISOLATES, f'cc_graph_pa_{n}_choose_{d}_isolates.txt', verbose=True)
        return ret
      else:
        dump_pa(ret, f'cc_graph_pa_{n}_choose_{d}_failed.txt', verbose=True)
        dump_pa(ISOLATES, f'cc_graph_pa_{n}_choose_{d}_isolates.txt', verbose=True)
        print('Failed to verify!!!')
        input()


from sys import argv
TIMEOUT = 10
main(int(argv[1]))
