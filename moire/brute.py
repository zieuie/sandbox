from copy import deepcopy
import itertools as it
from lib import separated, dumb_pa
import random


def brute(A, H):
  n = len(A[0])
  d = len(H[0])
  highs = list(range(n-d, n))
  A = deepcopy(A)
  best = 0

  def recur(i):
    nonlocal best
    if i > best:
      print(len(A), i)
      best = i
      # yield deepcopy(A[:i])

    if i >= len(A):
      yield deepcopy(A)
      return

    a = A[i]
    for h in it.permutations(H[i]):
      for x,p in enumerate(h):
        a[p] = highs[x]

      for v in A[:i]:
        if not separated(a,v,d):
          break
      else:
        yield from recur(i+1)

  yield from recur(0)


def lattify(t, H, n):
  pa = []
  row = [0]*n
  for x,h in enumerate(H):
    j = 0
    for i in range(n):
      if i not in h:
        row[i] = t[j]
        j += 1
    pa.append(deepcopy(row))
  return pa


def main():
  # n, d = 5, 4
  # n, d = 4, 3
  # n, d = 6, 4
  n, d = 7, 3
  # n, d = 9, 5
  # n, d = 9, 5
  # n, d = 6, 5
  # n, d = 8, 4
  print(n, d)
  H = list(it.combinations(list(range(n)), d))
  asdf = len(H) // 3
  # H = H[asdf:] + H[:asdf]
  # print(len(H))
  # H = H[5:] + H[:5]
  # H = H[5:] + H[:5]
  # H = H[15:] + H[:15]
  # H = H[40:] + H[:40]
  # H = H[15:] + H[:15]
  # H = H[::2] + H[1::2]
  # random.shuffle(H)

  # for t in it.permutations(list(range(n-d))):
  t = list(range(n-d))
  print ('Trying to infill', t)
  A = lattify(t, H, n)
  for pa in brute(A, H):
    print ('-'*10)
    for row in pa:
      print(row)
    print ('-'*10)
    if len(pa) == len(A):
      break
  else:
    print('failed')


main()