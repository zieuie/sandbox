
import itertools as it
import math


def ceildiv(n,d):
  # return n//d + int(bool(n%d))
  return math.ceil(n/d)


def weave_template(n,d):
  # weave template
  A = [[ceildiv(n,d)-1]*(n%d)]
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



def separated(u, v, d):
  dd = d*d
  i = 0
  while i < len(u) and (u[i]-v[i])**2 < dd:
    i += 1
  return i < len(u)


def infills(n,d,t):
  sofar = []
  def recur():
    if len(sofar) >= n:
      yield sofar
      return
    c = t[len(sofar)]
    for x in range(c*d, min(n, (c+1)*d)):
      if x not in sofar:
        sofar.append(x)
        yield from recur()
        sofar.pop()
  yield from recur()


def all_infills(n,d,T,sofar=None):
  sofar = sofar or []
  # sofar.append(next(infills(n,d,A[0])))
  # sofar.append(next(infills(n,d,A[1])))

  def recur():
    if len(sofar) >= len(T):
      yield sofar
      return

    a = T[len(sofar)]

    for u in infills(n,d,a):
      for v in sofar:
        s = separated(u,v,d)
        if not s:
          break
      else:
        sofar.append(u)
        yield from recur()
        sofar.pop()
  yield from recur()


# n,d = 5,3
n,d = 6,4
T = weave_template(n,d)
for A in all_infills(n, d, T):
  print()
  print('---')
  for row in A:
    print(row)
