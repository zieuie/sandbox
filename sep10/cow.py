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

