
import itertools as it
from random import shuffle

def fillings(ps, r, n, k):
  for pps in it.permutations(ps):
    ret = [0]*(n+k)
    for p,e in zip(pps, range(n, n+k)):
      ret[p] = e
    i = 0
    for x in range(n+k):
      if not ret[x]:
        ret[x] = r[i]
        i += 1
    yield ret
    # return

def separated(a, b):
  for u,v in zip(a,b):
    if abs(u-v) >= 3:
      return True

def block_separated(A, x):
  for a in A:
    if not separated(a, x):
      return False
  return True


def coward(r, k):
  n = len(r)
  # merp = list(it.combinations(list(range(n+k)), k))
  merp = []
  # for x in range(k-1, n+k):
  #   merp.append([0, 1, x])

  for x in range(0, n-1):
    merp.append([x, n-1, n])
  for x in range(n+1, n+k):
    merp.append([x, n-1, n])

  for x in range(0, n+k-2):
    merp.append([x, n+k-2, n+k-1])

  # print(merp)
  # input()

  def recur(sofar):
    # print(len(merp), len(sofar), sofar)
    if len(sofar) >= len(merp):
      yield sofar
      return

    ps = merp[len(sofar)]
    for f in fillings(ps, r, n, k):
      if block_separated(sofar, f):
        yield from recur(sofar+[f])
        # break
  yield from recur([])

for i, r in enumerate(it.permutations(list(range(6)))):
  # print(i, r)
  # for _ in coward(r, 3):
  #   break
  # else:
  #   print (r, 'Here!')
  #   input()

  # ret = sum(1 for _ in coward(r, 3))
  # print(r, ret)

  # if ret != 1:
  #   print(r, ret)

  for pa in coward(r, 3):
    print()
    for row in pa:
      print(row)
    input()