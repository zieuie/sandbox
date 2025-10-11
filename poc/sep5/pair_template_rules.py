import itertools as it
from collections import defaultdict, Counter


COLORS = [
  '\033[91m', # red
  '\033[92m', # green
  '\033[93m', # yellow
  '\033[94m',
  '\033[95m',
  '\033[96m',
  '\033[97m',
]

NC = '\033[0m'

def color(s, x):
  return COLORS[x] + s + NC


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


n,d = 8,3
T = weave_template(n,d)
Q = defaultdict(lambda: defaultdict(set))
C = Counter()
G = 0
C2 = Counter()
for vx, v in enumerate(T):
  for ux in range(vx):
    auto = False
    comb = set()
    for c, (a,b) in enumerate(zip(T[ux], v)):
      if abs(a - b) > 1:
        auto = True
      elif abs(a-b) == 1:
        comb.add(c)
    if auto:
      G += 1
    else:
      Q[ux][vx] = comb
      C.update([len(comb)])
      if len(comb) == 2:
        C2.update([ux, vx])

print(G)
print(C)
print(Counter(C2.values()))