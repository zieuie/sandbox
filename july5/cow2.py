
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


def in_identity_block(pot, d):
  for x,e in enumerate(pot):
    if x//d != e//d:
      return False
  return True


def dumb12(n=12, d=3):
  ret = 0
  for pot in it.permutations(list(range(n))):
    for x,e in enumerate(pot):
      if abs(e-x) >= d:
        break
    else:
      if not in_identity_block(pot,d):
        ret += 1
        print(ret)

  print('Done:',ret)


dumb12()

# for n in range(6, 20, 2):
#   smart (n)
  # dumb  (n)
  # dumber(n)

'''
start with identity
keep 1, 2, 3 fixed
the block is all permutations that have 123 in the first three positions, 456 in the next three, 789 in the third block, 10 11 12 in the last three. 

Can you calculate under distance 2 or less
Things that are not separated at distance 3
and are in different blocks.

When you're looking at all possible permutations, then any permutation in the block, don't consider it


'''