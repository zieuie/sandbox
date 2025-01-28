# Let's verify that ruskey even works


# generate all strings
def all_strings(n, k, d):
  if n >= d > 0:  # still have symbols to make
    # zeroes
    for pot in all_strings(n-1, k, d):
      yield [0] + pot

    # nonzeros
    for pot in all_strings(n-1, k, d-1):
      for q in range(1, k):
        yield [q] + pot

  elif d == 0:
    yield [0]*n


# return a string into a value
def kary_value(a, k):
  ret = 0
  for e in a:
    ret *= k
    ret += e
  return ret


# turn a value back into a string
def undo(val, n, k):
  ret = []
  for _ in range(n):
    ret = [val%k] + ret
    val //= k
  return ret


# how many left shifts would make this minimal?
def smallest_rotation(a, k):
  pot = kary_value(a, k)
  val, idx = pot, 0
  mask = pow(k, len(a))
  for x, e in enumerate(a):
    pot = (pot*k+e) % mask
    # print(x, e, undo(pot, n, k), pot, val, idx)
    if pot < val:
      idx = x+1
      val = pot
  return val, idx


# a = [1, 0, 2, 1, 0, 0, 0, 0, 0]
# n = len(a)
# k = 3

# print (smallest_rotation(a, k))

from ruskey import fix #, ruskey

# n, k, d = 7, 3, 3
n, k, d = 5, 3, 3

orbits = dict()
for pot in all_strings(n, k, d):
  val, _ = smallest_rotation(pot, k)
  orbits.setdefault(val, set()).add(tuple(pot))

o2 = {k:[] for k in orbits}

# for pot in fix(n, k, d):
for pot in fix(n, k, d):
  val, _ = smallest_rotation(pot, k)
  o2.setdefault(val, []).append(pot)

# for a,v in orbits.items():
#   print(undo(a, n, k), len(v), v)

for a,v in o2.items():
  print(undo(a, n, k), len(orbits[a]), v)
  # print(undo(a, n, k), orbits[val], v)
