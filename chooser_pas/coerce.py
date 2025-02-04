# create a fully filled Chebyshev PA by brute force

from collections import Counter
from lib import *


# n, d = 12, 6
n, d = 10, 5
dd = d*d
filename = f'pa_{n}_choose_{d}.txt'
# filename = f'dump88.txt'
pa = load_pa(filename)
A = pa


asdf = dict()
s = [set() for _ in A]
for vx in range(len(A)):
  for ux in range(vx):
    if ux != vx:
      count = 0
      for a,b in zip(A[ux],A[vx]):
        if (a-b)**2 >= dd:
          count += 1
      if count > 1:
        asdf.setdefault(count, []).append((ux,vx))
print(list(reversed(sorted(asdf.keys()))))

# for k,v in asdf.items():
#   print(k, len(v))
# print(asdf[11])
# print(sum(len(v) for v in asdf.values()))
# print(len(A)*(len(A)-1))

print(list(reversed(sorted((k,len(v)) for k,v in asdf.items()))))

def one():
  s = init_separations(pa, d)
  removals = []
  for x,si in enumerate(s):
    if len(si) != len(pa)-1:
      removals.append(x)

  print(removals)
  for r in removals:
    row = [0]*n
    for v in pa:
      for x,(a,b) in enumerate(zip(pa[r],v)):
        if (a-b)**2 >= dd:
          row[x] += 1
    print (r, pa[r], row)


  pot = [9, 5, 3, 4, 8, 0, 1, 6, 2, 7, 10, 11]
  row = [0]*n
  for v in pa:
    for x,(a,b) in enumerate(zip(pot,v)):
      if (a-b)**2 >= dd:
        row[x] += 1

  print (111, pot, row)


