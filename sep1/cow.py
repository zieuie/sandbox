import math
import itertools as it


def chase(n, k):
    """
    Generate all k-combinations of {1,...,n} in revolving-door order.
    Each successive set differs by exchanging exactly one element.
    """
    # Initialize with the first combination
    comb = list(range(1, k + 1))
    yield comb[:]

    while True:
        # Step 1: find rightmost element that can be incremented
        for j in range(k - 1, -1, -1):
            if comb[j] != n - k + j + 1:
                break
        else:
            return  # all combinations generated

        # Step 2: increment it
        comb[j] += 1

        # Step 3: reset the following elements
        if (k - j) % 2 == 1:  # odd distance → reset leftward
            for i in range(j - 1, -1, -1):
                comb[i] = i + 1
        else:  # even distance → reset rightward
            for i in range(j + 1, k):
                comb[i] = comb[i - 1] + 1

        yield comb[:]


def init_foes(T):
  lut = [set() for _ in T]
  for vx,v in enumerate(T):
    for ux in range(vx):
      sep = False
      for x,y in zip(T[ux],v):
        if abs(x - y) > 1:
          sep = True
          break
      if not sep:
        lut[ux].add(vx)
        lut[vx].add(ux)
  return lut


def ceildiv(n,d):
  return n//d + int(bool(n%d))


def weave_template(n,d):
  # weave template
  H = 0
  A = [[ceildiv(n,d)-1]*(n%d or d)]
  for x in range(ceildiv(n,d)-1):
    H = len(A)
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
  return A, H


T, H = weave_template(9, 3)
# for row in T:
#    print(row)


foes = init_foes(T)
print(H, len(foes))
for row, foe in zip(T, foes):
    print(row)
    x = [['.']*(len(foes)//H) for _ in range(H)]
    cols = [0]*(len(foes)//H)
    for e in foe:
      x[e%H][e//H] = 'x'
      # cols[e//H] += 1
      cols[e%H] += 1
    
    print(sorted(cols))
    for row in x:
      print(''.join(row))
    input()

#    print(row, foe)
# print(len(foes))


