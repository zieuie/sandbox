import re

# def load_pa(filename):
#   ret = []
#   with open(filename, 'r') as f:
#     for line in f:
#       line = line.strip()
#       if not line or line[0] == '#':
#         continue
#       pot = list(map(int, re.sub(r'[^\d ]', '', line).split()))
#       if pot:
#         ret.append(pot)
#   return ret


# A = load_pa('examples_5_3.txt')
# print(len(A))

# pots = [A[x:x+10] for x in range(0,len(A),10)]

# # print(len(pots))
# # print(list(map(len,pots)))

from all_infills import weave_template

n,d = 8,3
# n,d = 9,3
A = weave_template(n,d)
# for a in A:
#   print(a)


# The things that aren't separated by template
from collections import Counter, defaultdict
lut = defaultdict(set)
for vx,v in enumerate(A):
  for ux in range(vx):
    u = A[ux]
    if max([abs(x-y) for x,y in zip(u,v)]) < 2:
      lut[ux].add(vx)
      lut[vx].add(ux)


# print (n,d,Counter(map(len, lut.values())))
for k, vs in lut.items():
  # print(k,vs)
  qwer = Counter()
  a = [int(e==0) for e in A[k]]
  for v in vs:
    b = [int(e==0) for e in A[v]]
    qwer.update([tuple(b)])
    if a == b:
      print (A[k], A[v])
  print(len(vs), a, qwer) # sorted(qwer.values()))
  break

print(len(lut))

# k = 0
# vs = lut[k]
# print(A[k])
# a = [e<5 for e in A[k]]
# print(a)
# # for v in vs:
# #   b = [e<5 for e in A[v]]
# #   print(a,b)

