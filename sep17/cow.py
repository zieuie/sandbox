import re
import itertools as it

# for ps in it.combinations(list(range(8)), 5):
#   t = ['H']*8
#   for p in ps:
#     t[p] = 'L'
#   print(' '.join(t))

# exit(0)

blobs = []
A = []
with open('moo.txt', 'r') as f:
  for line in f:
    row = list(map(int, re.sub(r'[^\d]', ' ', line).strip().split()))
    if len(row) > 1:
      A.append(row)
    else:
      if A:
        blobs.append(A)
        A = []

if A:
  blobs.append(A)

S = '''
H H H L L L L L
H L L H H L L L
L H L H H L L L
L L H H H L L L

'''
pines = []
for line in S.split('\n'):
  line = line.strip().split()
  if not line:
    continue
  pines.append(line)

pprint = lambda r: print(' '.join(map(str, r)))

a = blobs[0]

for p in pines:
  print()
  for row in a:
    h = iter([0,1,2])
    l = iter(row)
    t = [next(h) if e == 'H' else next(l)+3 for e in p]
    pprint(t)

exit(0)

# for A in blocks:
#   print()
#   for row in A:
#     print(row)

# print(len(blobs))
# print(list(map(len,blobs)))

cols = set()
for b in blobs:
  for i in range(5):
    c = []
    for row in b:
      c.append(row[i])
    cols.add(tuple(c))

kets = set()
for b in blobs:
  for i in range(5):
    for j in range(5):
      if i == j:
        continue
      ding = []
      for row in b:
        if row[i] == 4:
          ding.append(row[j])
      kets.add(tuple(sorted(ding)))

# print(kets)

# for c in cols:
#   print(c)
# print(len(cols))

# replace 1s with 5/6. Replace 2 with some column of a blob
Z = []
Z.append(list(map(int, '1 2 7 1'.split())))
Z.append(list(map(int, '7 1 2 1'.split())))
Z.append(list(map(int, '7 1 1 2'.split())))

sofar = []
def recur():
  if len(sofar) != 2:
    print('recur', len(sofar))
  
  if len(sofar) >= len(Z):
    yield sofar
    return

  for ss in ((5,6), (6,5)):
    for c in cols:
      block = []
      separated = True
      for two in c:
        # build the row
        row = []
        sss = iter(ss)
        for r in Z[len(sofar)]:
          if r == 1:
            row.append(next(sss))
          elif r == 2:
            row.append(two)
          elif r == 7:
            row.append(7)

        # check that the row is separated
        for sb in sofar:
          for op in sb:
            if max(abs(x-y) for x,y in zip(row,op)) < 3:
              separated = False
              break
        
        # this block doesn't work
        if not separated:
          break
        block.append(row)

      # skip this block
      if not separated:
        continue

      # try this block
      sofar.append(block)
      yield from recur()
      sofar.pop()

for soln in recur():
  print('---')
  for row in soln:
    print(row)

  input('Found something')