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
pair_map = defaultdict(set)
pair_counter = Counter()
for ux, u in enumerate(A):
  for vx in range(ux):
    d = distance(u, A[vx])
    if d < goald:
      pair_map[ux].add(vx)
      pair_map[vx].add(ux)
      pair_counter.update((ux,vx))

to_delete = []
while pair_counter:
  u, _ = pair_counter.most_common(1)[0]
  neighbors = pair_map[u]
  if len(neighbors) == 0:
    break
  for v in neighbors:
    pair_map[v].discard(u)
    pair_counter.subtract([v])
  del pair_counter[u]
  del pair_map[u]
  to_delete.append(u)

to_delete = sorted(to_delete)
for idx in reversed(to_delete):
  del A[idx]

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