import itertools as it

S = '''
33142
12334
13342
14323
23134
31342
31423
33214
33412
31234
'''.strip()

A = [list(map(int, r)) for r in S.split()]

def do_all():
  for T in it.product(*[(it.permutations((2,3))) for _ in range(len(A))]):
    # print(list(T))
    # input()
    B = []
    for row, threes in zip(A, T):
      threes = iter((2,3))
      u = []
      for e in row:
        if e == 1:
          u.append(0)
        elif e == 2:
          u.append(1)
        elif e == 3:
          u.append(next(threes))
        elif e == 4:
          u.append(4)
      B.append(u)
    yield B

# for row in B:
#   print(row)

for B in do_all():
  good = True
  for ux in range(len(B)):
    for vx in range(ux):
      if max(abs(a-b) for a,b in zip(B[ux], B[vx])) < 3:
        good = False
        break
    if not good:
      break
  # if good:
  if True:
    print('---')
    for row in B:
      print(row)
    input()
