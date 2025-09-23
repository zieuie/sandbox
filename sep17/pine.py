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


def kitty(pines):
  sofar = []
  def recur():
    depth = len(sofar)
    if depth >= len(pines):
      yield sofar
      return
    pine = pines[depth]
    # for hs in it.permutations(list(range(5,7))) if depth else ((6,5),):
    for hs in it.permutations(list(range(5,8))):
    # for hs in it.permutations(list(range(5,8))) if depth else ((5,6,7),):
      for z, blob in enumerate(blobs):
        # for perm in it.permutations(list(range(5))) if depth else ((0,1,2,3,4),):
        for perm in it.permutations(list(range(5))):
          block = []
          sep = True
          for row in blob:
            lows = iter(row[e] for e in perm)
            # lows = iter(row)
            highs = iter(hs)
            u = [next(lows) if e == 2 else next(highs) if e == 1 else e for e in pine]
            for other_block in sofar if depth else tuple():
              for v in other_block:
                if max(abs(x-y) for x,y in zip(u,v)) < 3:
                  sep = False
                  break
              if not sep:
                break
            if not sep:
              break
            block.append(u)
          if not sep:
            continue
            
          # print(depth, hs, perm, z)
          # print(depth, hs, z)
          sofar.append(block)
          yield from recur()
          sofar.pop()
  yield from recur()


first = [
  [1, 2, 2, 2, 2, 2, 1, 1],
  [1, 2, 2, 2, 2, 1, 2, 1],
  [1, 2, 2, 2, 2, 1, 1, 2],
]

second = [
  [1, 2, 2, 1, 2, 2, 2, 1],
  [1, 2, 2, 1, 2, 2, 1, 2],
  [1, 2, 2, 1, 2, 1, 2, 2],
]

notes = [
  [2, 2, 2, 2, 7, 1, 1, 2],
  [2, 2, 2, 2, 7, 1, 2, 1],
  [2, 2, 2, 2, 1, 2, 7, 1],
  [2, 2, 2, 2, 2, 7, 1, 1],
]

ff = list(kitty(first))
print(ff, len(ff))
ss = list(kitty(second))
print(ss, len(ss))

# for soln in kitty(notes):
for soln in it.product(ff, ss):
  print('---')
  for block in soln:
    for row in block:
      print(row)
  break