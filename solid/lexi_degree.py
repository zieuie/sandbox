
'''
For length n and distance d = n//2

Given a graph G where V = all permutations and (u,v) \in E when
  u,v has distance < d and u,v do not have the high symbols in
  the choice of same columns,

  print the degree of the identity permutation,
  which supposedly is the same as the degree of any other vertex
'''

import itertools as it


def distance(u, v):
  return max(abs(a-b) for a,b in zip(u,v))


def smart(n=6):
  d = n//2

  ret = 0
  perm = []
  lows = []
  used = [False] * n

  # perm[:i] is not separated from identity
  def recur():
    nonlocal ret

    # the prefix is in our clique
    if len(lows) == d and lows[-1] == d-1:
      return

    # no symbols remain
    if len(perm) >= n:
      ret += 1
      return

    for x in range(n):
      # skip if used
      if used[x] or abs(x-len(perm)) >= d:
        continue

      if x < d:
        lows.append(len(perm))

      perm.append(x)
      used[x] = True
      recur()
      used[x] = False
      perm.pop()

      if x < d:
        lows.pop()

  recur()
  print(ret)


def dumb(n=6):
  d = n//2
  ret = 0
  for pot in it.permutations(list(range(n))):
    r = 0
    for x,e in enumerate(pot):
      if e < d:
        r=x
      if abs(e-x) >= d:
        r = d-1
        break

    if r != d-1:
      ret += 1

  print(ret)


def dumber(n=6):
  d = n//2
  ret = 0
  I = list(range(n))
  for pot in it.permutations(list(range(n))):
    if distance(I, pot) < d and [x for x,e in enumerate(pot) if e < d][-1] >= d:
      ret += 1
  print(ret)


'''
vvv^^^
v^^vv^

012345


'''
def smart2(n):
  d = n//2
  ret = 0
  for hs in it.combinations(list(range(n)), d):
    for x in range(n):
      if x < d and x not in hs:
        pass


for n in range(6, 20, 2):
  smart (n)
  # dumb  (n)
  # dumber(n)