
'''
Given a PA without the distance, remove rows until it does.
'''

import re
import sys
from collections import Counter, defaultdict


def distance(a, b):
  ret = 0
  for u,v in zip(a,b):
    ret = max(abs(u-v), ret)
  return ret


# get args
filename = sys.argv[1]
goald = int(sys.argv[2])

# read PA
A = []
with open(filename, 'r') as f:
  for line in f:
    line = line.strip()
    if not line or line.startswith('#'):
      continue
    line = re.sub(r'[^\d\s]', '', line)
    A.append(list(map(int, line.split())))

# validate rows
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


# get pairs
sofar = []
for ux, u in enumerate(A):
  for vx, v in enumerate(A):
    if ux == vx:
      continue
    if distance(u, v) < goald:
      break
  else:
    sofar.append(u)
A = sofar

for ux, u in enumerate(A):
  for vx in range(ux):
    d = distance(u, A[vx])
    if d < goald:
      print ('Somehow, trimming failed. Send this PA to Zooey.')
      exit(1)

with open(f'trimmed_{goald}_from_{filename}', 'w+') as f:
  for row in A:
    f.write(' '.join(map(str, row)) + '\n')

print (f'Verified new ({n},{goald})-PA of size {len(A)}')