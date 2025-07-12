
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
      print('success for', mid)
      return pot
  print('failure for', mid, len(ret), d)
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

# @functools.cache
def all_goods(hn,ln,n,d):
  ret = []
  lcms = list(it.combinations(list(range(0, d)), ln))
  # random.shuffle(lcms)
  hcms = list(it.combinations(list(range(d, n)), hn))
  # random.shuffle(hcms)

  lshs = list(it.product(lcms, hcms))
  random.shuffle(lshs)

  # for ls in lcms:
  #   for hs in hcms:
  for ls,hs in lshs:
      mid2 = tuple(sorted(set(range(n)) - set(hs) - set(ls)))
      if not can_construct(mid2, d):
        continue

      lpms = list(it.permutations(ls))
      # random.shuffle(lpms)
      hpms = list(it.permutations(hs))
      # random.shuffle(hpms)

      lphp = list(it.product(lpms, hpms))
      random.shuffle(lphp)
      # for ls2 in lpms:
      #   for hs2 in hpms:
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

    if time() - START_TIME > TIMEOUT:
      return

    block = len(sofar)

    # print('>'*(block+1), block, 'of', len(Z))
    if block > DEEPEST:
      DEEPEST = block
      # print('>'*(block+1), block, 'of', len(Z))
      print(datetime.now(), n, d, TIMEOUT, block, 'of', len(Z))

    # if we're done, yield!
    if block >= len(Z):
      yield copy.deepcopy(sofar)
      return

    pre, suf, hn, ln = Z[block]
    for hs, ls, mid2 in all_goods(hn,ln,n,d):
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

  while True:
    START_TIME = time()
    for pot in fillz(Z,n):
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
    # else:
      # print ('Restart!', n, d, TIMEOUT, DEEPEST)
      # TIMEOUT = max(0.5 ,DEEPEST)


# for n in range(6, 10, 2):
for n in range(6, 100, 2):
  TIMEOUT = .5
  main(n)
