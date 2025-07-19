
import copy
import functools
import itertools as it
import random
from collections import *
from datetime import datetime
from time import time

import networkx as nx
from hill import hill_climb_driver, separated2, verify2
from lib import *


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
    mid = tuple(sorted(set(range(n)) - set(pre) - set(suf)))
    ret[ccid] = (pre, suf, mid)
  return ret


def do_shift(row, f):
  return [f[e] for e in row]


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
      return pot
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


def all_goods(pre,suf,hn,ln,n,d):
  ret = []
  lcms = list(it.combinations(list(range(0, d)), ln))
  hcms = list(it.combinations(list(range(d, n)), hn))
  for ls,hs in it.product(lcms, hcms):
    mid2 = tuple(sorted(set(range(n)) - set(hs) - set(ls)))
    if not can_construct(mid2, d):
      continue

    lpms = list(it.permutations(ls))
    hpms = list(it.permutations(hs))
    for ls2, hs2 in it.product(lpms, hpms):
      nexpre, nexsuf = cow(hs2, ls2, pre, suf, d)
      ret.append((nexpre, nexsuf, mid2))
  return ret


DEEPEST = -1
def fillz(Z,n,d):
  sofar = []
  def recur():
    global START_TIME
    global TIMEOUT
    global DEEPEST

    block = len(sofar)
    if block > DEEPEST:
      DEEPEST = block
      for pre, suf, _,_ in sofar:
        m = n - len(pre) - len(suf)
        print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))
      print(datetime.now(), n, d, TIMEOUT, block, 'of', len(Z))

    # if we're done, yield!
    if block >= len(Z):
      yield copy.deepcopy(sofar)
      return

    for nexpre, nexsuf, mid2 in Z[block]:
      if TIMEOUT and time() - START_TIME > TIMEOUT:
        return
      nex = nexpre + [-1]*len(mid2) + nexsuf
      for t in sofar:
        if not separated2(nex, t[-1], d):
          break
      else:
        sofar.append((nexpre, nexsuf, mid2, nex))
        yield from recur()
        sofar.pop()

  yield from recur()


def dfs_retry(Z,n,d):
  while True:
    # random.shuffle(Z)
    START_TIME = time()
    print(START_TIME, 'Iterate!')
    for pot in fillz(Z,n,d):
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
      return ret


def separated3(u,v,d):
  up,us,um = u
  vp,vs,vm = v
  return separated2(up+[-1]*len(um)+us, vp+[-1]*len(vm)+vs, d)


def dfs_retry2(Z,n,d):
  Z.sort(key=len, reverse=True)
  for x,row in enumerate(Z):
    print(x, len(row))
    random.shuffle(row)

  sofar = []
  def recur():
    block = len(sofar)
    if block >= len(Z):
      yield True
      return

    for nexpre, nexsuf, mid2 in Z[block]:
      nex = nexpre + [-1]*len(mid2) + nexsuf
      for t in sofar:
        if not separated2(nex, t[-1], d):
          break
      else:
        sofar.append((nexpre, nexsuf, mid2, nex))
        yield from recur()
        sofar.pop()

  ret = next(recur())
  if ret:
    for line in sofar:
      print(line)
  return ret


def graph_approach(Z,n,d):
  Z.sort(key=len)
  for x,row in enumerate(Z):
    print(x, len(row))

  pre = [0]
  for e in Z:
    pre.append(pre[-1]+len(e))

  print (datetime.now(), 'Building edges')
  edges = []
  for cv in range(len(Z)):
    for cu in range(cv):
      for v in range(len(Z[cv])):
        for u in range(len(Z[cu])):
          if separated3(Z[cu][u],Z[cv][v],d):
            edges.append((pre[cu]+u, pre[cv]+v))

  print (datetime.now(), 'Edges', sum(map(len,edges)))
  print (datetime.now(), 'Building graph')

  G = nx.Graph()
  G.add_edges_from(edges)
  print (datetime.now(), 'Starting approximation')
  # clique = nx.approximation.max_clique(G)
  # if len(clique) < len(Z):
  #   print (datetime.now(), 'Approximation was only', len(clique), 'but should be', len(Z))
  # else:
  #   print (datetime.now(), 'Approximation worked!', clique)
  #   # return
  
  print(datetime.now(), 'Starting clique finding')
  for clique in nx.find_cliques(G):
    if len(clique) == len(Z):
      break
  
  print(datetime.now(), 'Found!', clique)
  # cc = Counter(map(len, nx.find_cliques(G)))
  # print(datetime.now(), 'Cliques:', sorted(cc.items()))

  print(datetime.now(), 'Pre', pre)
  print(datetime.now(), 'Z', list(map(len, Z)))
  ret = []
  for e in clique:
    for x in range(len(Z)):
      if pre[x+1] > e:
        pre2, suf2, mid2 = Z[x][e-pre[x]]
        # ret.append(Z[x][e-pre[x]])
        for row in can_construct(mid2, d):
          nex = pre2 + row + suf2
          ret.append(nex)
        break
  print(datetime.now(), 'ret', ret)

  return ret


def demo(Z,n,d):
  Z.sort(key=len)
  for x,row in enumerate(Z):
    print(x, len(row))


def main(n):
  # make connected components
  d = n//2
  A = make_array(n,d)
  C = make_components(A,d)
  
  H = make_fixes(C,A,n)

  print ('Connected components:')
  for k,(pre, suf, mid) in H.items():
    if mid:
      print(' '.join(map(str, it.chain(pre, ['.']*len(mid), suf))), '| Total:', len(C[k]))

  F = list(H.values())

  F = sorted(F, key=lambda t: -len(t[-1]))
  Z = []
  Y = []
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
    
    goods = all_goods(pre, suf, hn, ln, n, d)
    Y.append((tuple(pre),tuple(suf)))
    Z.append(goods)

  print ()
  print ('New Graph:')
  for z, (pre, suf) in zip(Z, Y):
    m = n - len(pre) - len(suf)
    print()
    print(' '.join(map(str, it.chain(pre, ['.']*m, suf))), '| Total:', len(z)) #, f'({m} choose {m//2} is {nCr(m, m//2)})')
    # for p2,s2,m2 in z:
    #   print('   ', ' '.join(map(str, it.chain(p2, ['.']*len(m2), s2))))

  # print ()
  # print ('Prefixes:')
  # for l, (pre, suf) in zip(map(len, Z), Y):
  #   m = n - len(pre) - len(suf)
  #   print(' '.join(map(str, it.chain(pre, ['.']*m, suf))), '| Total:', l) #, f'({m} choose {m//2} is {nCr(m, m//2)})')
  
  print ()
  print ('Isolates:', len(ISOLATES))

  print ()

  # print()
  # for pre, suf in Y:
  #   random.shuffle(Z)
  #   m = n - len(pre) - len(suf)
  #   print(' '.join(map(str, it.chain(pre, ['.']*m, suf))))

  # ret = dfs_retry(Z,n,d)
  # ret = dfs_retry2(Z,n,d)
  # ret = graph_approach(Z,n,d)
  ret = demo(Z,n,d)

  if ret is None:
    return

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

TIMEOUT = 0
main(int(argv[1]))
