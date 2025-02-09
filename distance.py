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

pairs = []
n = None
for x, row in enumerate(A):
  if n is None:
    n = len(row)
  
  if len(row) != n:
    print(f'Invalid length of row {x+1}')
    exit(1)
  if len(set(row)) != n:
    print(f'Duplicate in row {x+1}')
    exit(1)
  if max(row) != n-1:
    print(f'Invalid max in row {x+1}')
    exit(1)
  if min(row) != 0:
    print(f'Invalid min in row {x+1}')
    exit(1)

pairs = []
ret = float('inf')
for ux, u in enumerate(A):
  for vx in range(ux):
    d = distance(u, A[vx])
    if d < ret:
      ret = d
      pairs = [(ux,vx)]
      print(f'New minimum distance: {ret}')
    elif d == ret:
      pairs.append((ux,vx))

if len(pairs) < 20:
  print(f'Distance {ret} happens at these pairs of rows:')
  for ux,vx in pairs:
    print(f'  - {ux}, {vx}')
  print()
  print(f'Distance is {ret}')
else:
  print(f'Distance {ret} happens in {len(pairs)} pairs of rows')
  print(f'Minimum distance is {ret}')


