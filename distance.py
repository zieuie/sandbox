import re
import sys


def distance(a, b):
  ret = 0
  for u,v in zip(a,b):
    ret = max(abs(u-v), ret)
  return ret


A = []
with open(sys.argv[1]) as f:
  for line in f:
    line = line.strip()
    if not line or line.startswith('#'):
      continue
    line = re.sub(r'[^\d\s]', '', line)
    A.append(list(map(int, line.split())))

n = None
for row in A:
  if n is None:
    n = len(row)
  
  if len(row) != n:
    print('Invalid length of row')
    exit(1)
  if len(set(row)) != n:
    print('Duplicate in row')
    exit(1)
  if max(row) != n-1:
    print('Invalid max in row')
    exit(1)
  if min(row) != 0:
    print('Invalid min in row')
    exit(1)

ret = float('inf')
for ux, u in enumerate(A):
  for vx in range(ux):
    ret = min(ret, distance(u, A[vx]))
print(ret)
