import re
import itertools as it
import random


n, d, g = 9, 3, 20
A = []
with open('9_3.txt', 'r') as f:
  for line in f:
    line = re.sub(r'[^\w ]', '', line.strip())
    A.append(list(map(int, line.split())))


# for row in A:
#   print(row)
# print(len(A))


H = list(it.combinations(list(range(n)), d))

B = dict()
for r in H:
  B[r] = []

for row in A:
  censor = tuple( i for i,e in enumerate(row) if e >= n-d )
  # B.setdefault(censor, list()).append(row)
  B[censor].append(row)

# print(len(B))

lows = list(range(n-d))
highs = list(range(n-d, n))
for k,v in B.items():
  # print(k, len(v))
  while len(v) < g:
    l, h = 0, 0
    t = [0]*n
    random.shuffle(lows)
    random.shuffle(highs)
    for i in range(n):
      if i in k:
        t[i] = highs[h]
        h += 1
      else:
        t[i] = lows[l]
        l += 1
    v.append(t)

# print(len(B))

pa = []
for x in range(g):
  for h in H:
    pa.append(B[h][x])

# for row in pa:
  # censor = tuple( i for i,e in enumerate(row) if e >= n-d )
  # print(row, censor)
  # print(row)

filename = f'pa_{n}_choose_{d}_times_{g}.txt'
with open(filename, 'w+') as f:
  for row in pa:
    f.write(' '.join(map(str, row)) + '\n')
