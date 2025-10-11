import re
from all_infills import weave_template


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      pot = list(map(int, re.sub(r'[^\d ]', '', line).split()))
      if pot:
        ret.append(pot)
  return ret


# The things that aren't separated by template
from collections import Counter, defaultdict
def make_foes(A):
  foes = defaultdict(set)
  for vx,v in enumerate(A):
    for ux in range(vx):
      u = A[ux]
      if max([abs(x-y) for x,y in zip(u,v)]) < 2:
        foes[ux].add(vx)
        foes[vx].add(ux)
  return foes


n,d = 8,3
A = weave_template(n,d)
foes = make_foes(A)


