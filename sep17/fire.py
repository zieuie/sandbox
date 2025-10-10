
import itertools as it

def block(A):
  for i,x in enumerate(A):
    for j in range(i):
      y = A[j]
      if x == y:
        return False

      sep = False
      for u,v in zip(x,y):
        if abs(u-v) >= 3:
          sep = True

      if not sep:
        return False

  for u in A:
    print(u)
  # input()
  return True

U = list(it.permutations([0,1,2,3]))
V = list(it.permutations([0,1,2,4]))

for a,b in ((2,2), (1,3), ())
  for uu in it.combinations(U, 2):
    for vv in it.combinations(V, 2):
      block([*uu, *vv])

