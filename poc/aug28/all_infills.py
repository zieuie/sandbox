
import itertools as it
from collections import defaultdict, Counter


def separated(u,v,d):
  for x,y in zip(u,v):
    if abs(x-y) >= d:
      return True
  return False


def infills(n,d,u):
  sofar = []
  def recur():
    if len(sofar) >= n:
      yield sofar
      return
    c = u[len(sofar)]
    for x in range(c*d, min(n, (c+1)*d)):
      if x not in sofar:
        sofar.append(x)
        yield from recur()
        sofar.pop()
  yield from recur()


def weave_template(n,d):
  A = [[0]*d]
  for x in range(1, n//d+1):
    k = d
    if len(A[0]) + k > n:
      k = n%d

    B = []
    for a in A:
      for ps in it.combinations(list(range(len(a)+k)), k):
        nex = []
        l = 0
        for i in range(len(a)+k):
          if i in ps:
            nex.append(x)
          else:
            nex.append(a[l])
            l += 1
        B.append(nex)
    A = B
  return A


def all_infills(n,d,A,sofar=None):
  sofar = sofar or []
  sofar.append(next(infills(n,d,A[0])))
  # sofar.append(next(infills(n,d,A[1])))

  def recur():
    if len(sofar) >= len(A):
      yield sofar
      return

    a = A[len(sofar)]
    # print('>'*len(sofar), 'sofar', sofar, 'a', a)

    for u in infills(n,d,a):
      # print('>'*len(sofar), 'u', u)
      for v in sofar:
        s = separated(u,v,d)
        if not s:
          break
      else:
        sofar.append(u)
        yield from recur()
        sofar.pop()
  yield from recur()


def main():
  n,d = 5,3
  # n,d = 8,3

  # for pot in infills(n,d,[1,1,0,0,0]):
  #   print('pot', pot)

  A = weave_template(n, d)

  if (n,d) == (8,3):
    ss = [0,1,0,1,2,0,1,2]
    A.remove(ss)
    A = [A[0]] + [ss] + A[1:]

  # for x, a in enumerate(A[:5]):
  #   print(x, a)
  # print('###')

  for x, pot in enumerate(all_infills(n,d,A)):
    print('---')
    for row in pot:
      print(row)
    # print(x)
    print()


if __name__ == '__main__':
  main()