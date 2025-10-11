'''
Hey so when you have a template, how many pairs of rows
aren't naturally separated by the template?
'''

from all_infills import weave_template

# n,d = 8,3
n,d = 9,3
A = weave_template(n,d)

from collections import Counter, defaultdict
lut = defaultdict(set)
for vx,v in enumerate(A):
  for ux in range(vx):
    u = A[ux]
    if max([abs(x-y) for x,y in zip(u,v)]) < 2:
      lut[ux].add(vx)
      lut[vx].add(ux)

print (n,d,Counter(map(len, lut.values())))