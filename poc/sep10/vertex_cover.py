import itertools as it
from collections import defaultdict, Counter


def ceildiv(n,d):
  return n//d + int(bool(n%d))


def weave_template(n,d):
  # weave template
  A = [[ceildiv(n,d)-1]*(n%d or d)]
  for x in range(ceildiv(n,d)-1):
    B = []
    for a in A:
      for ps in it.combinations(list(range(len(a)+d)), d):
        nex = []
        l = 0
        for i in range(len(a)+d):
          if i in ps:
            nex.append(x)
          else:
            nex.append(a[l])
            l += 1
        B.append(nex)
    A = B
  return A



# for ux in range(len(T)):
  # print()
  # u = T[ux]
  # print(ux, u)
  # if u == [0,0,0,1,1,1,2,2]:
    # print(ux)
    # input()
  # for k,v in lut[ux].items():
  #   print(k,len(v))

def quick_fill(t,n,d):
  f = [iter(range(e*d, (e+1)*d)) for e in range(ceildiv(n,d))]
  return [next(f[e]) for e in t]


def min_vertex_cover(lut):
  n = len(lut)

  # build edge list
  edges = []
  for u in range(n):
    for v in lut[u][2]:
      if u < v:  # avoid duplicates
        edges.append((u, v))

  best_cover = list(range(n))  # worst case: all vertices

  def dfs(covered_edges, cover, start):
    nonlocal best_cover
    # prune
    if len(cover) >= len(best_cover):
      return
    # check if all edges are covered
    if len(covered_edges) == len(edges):
      best_cover = cover[:]
      return

    # pick first uncovered edge
    for idx, e in enumerate(edges):
      if idx not in covered_edges:
        u, v = e
        break

    # branch: include u
    dfs(covered_edges | {i for i, (a, b) in enumerate(edges) if a == u or b == u},
        cover + [u], u + 1)
    # branch: include v
    dfs(covered_edges | {i for i, (a, b) in enumerate(edges) if a == v or b == v},
        cover + [v], v + 1)

  dfs(set(), [], 0)
  return best_cover

def approx_vertex_cover(lut):
    n = len(lut)
    edges = set()
    for u in range(n):
        for v in lut[u][2]:
            if u < v:
                edges.add((u, v))

    cover = set()
    while edges:
        u, v = edges.pop()
        cover.add(u)
        cover.add(v)
        # remove all edges incident to u or v
        edges = {e for e in edges if u not in e and v not in e}
    return list(cover)


# for x in lut:
#   print(x, [(k,len(v)) for k,v in lut[x].items()])
# exit(0)

# ux = 0
# # ux = 46
# # print(T[ux])
# print(' '.join(map(str, quick_fill(T[ux], 8,3))))
# for x in lut[ux][2]:
#   # print(T[x])
#   print(' '.join(map(str, quick_fill(T[x], 8,3))))

def greedy_vertex_cover(lut):
  cover = dict()
  kings = []
  for ux in range(len(lut)):
    qwer = True
    for x in lut[ux][2]:
      if x in cover:
        qwer = False
    if not qwer:
      continue
    
    kings.append(ux)
    cover[ux] = ux
    for x in lut[ux][2]:
      cover[x] = ux
  return kings

  # for ux,u in enumerate(T):
  #   if ux not in cover:
  #     print('missed', ux)

  # for k,v in cover.items():
  #   print(k, v)

  print(kings, len(kings))
  for k in kings:
    print(T[k])
  return kings

def veritify2(T, lut, kings):
  cover = dict()
  for ux in kings:
    # for x in lut[ux][2]:
    #   if x in cover:
    #     print('Redundant', ux, x)
    
    cover[ux] = ux
    for x in lut[ux][2]:
      cover[x] = ux


  for ux,u in enumerate(T):
    if ux not in cover:
      print('missed', ux)


def medium_vertex_cover(lut):
  reps = []
  frontier = set()
  fmax = 0
  def recur():
    nonlocal fmax
    if len(frontier) > fmax:
      fmax = len(frontier)
      print(len(frontier), len(reps))
    if len(lut) - len(frontier) < 10:
      yield reps
      return
    for ux in lut.keys():
      if ux in frontier:
        continue
      newbies = set()
      for x in lut[ux][2]:
        if x not in frontier:
          newbies.add(x)
      if len(newbies) > 14:
        reps.append(ux)
        frontier.update(newbies)
        yield from recur()
        frontier.difference_update(newbies)
        reps.pop()
  for x in recur():
    return x
  return None

T = weave_template(8,3)
lut = defaultdict(lambda: defaultdict(set))
for vx,v in enumerate(T):
  for ux in range(vx):
    u = T[ux]
    foe = True
    cols = 0
    for x,y in zip(u,v):
      if abs(x-y) > 1:
        foe = False
      if abs(x-y) > 0:
        cols += 1
    if foe:
      lut[ux][cols].add(vx)
      lut[vx][cols].add(ux)


n,d=8,3
print(' '.join(map(str, quick_fill(T[46],n,d))))
# print(T[46])
for u,vs in lut[46].items():
  # print(k, len(vs))
  for v in vs:
    # print(u,T[v])
    print(' '.join(map(str, quick_fill(T[v],n,d))))

exit(0)


# cover = min_vertex_cover(lut)
# cover = approx_vertex_cover(lut)
# cover = greedy_vertex_cover(lut)
cover = medium_vertex_cover(lut)
veritify2(T, lut, cover)