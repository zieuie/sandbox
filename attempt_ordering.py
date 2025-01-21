# Trying to see a way to generate these things in order

from collections import Counter
from lib import string_count, aperiodic, burnside, maximum_rotation, ternary_value

def all_strings(n, s, t):
  if n:
    if t:
      for pot in all_strings(n-1, s, t-1):
        yield [2] + pot
    if s:
      for pot in all_strings(n-1, s-1, t):
        yield [0] + pot
    for pot in all_strings(n-1, s, t):
      yield [1] + pot
  elif s == 0 and t == 0:
    yield []


## Configure
# n, s, t = 10, 2, 4
n, s, t = 5, 2, 3
n, s, t = 5, 2, 2
n, s, t = 5, 2, 1
n, s, t = 7, 2, 1
n, s, t = 7, 2, 3
# n, s, t = 5, 1, 1
# n, s, t = 12, 4, 4
# n, s, t = 30, 2, 2
# n, s, t = 6, 3, 3
# n, s, t = 6, 4, 2
print('Calculated strings:', string_count(n, s, t))

## Print all strings
every_string = list(all_strings(n, s, t))
lut = {ternary_value(w):x for x,w in enumerate(every_string)}

for x, w in enumerate(every_string):
  m = maximum_rotation(w, n)
  print (x,w,lut[m])


orbs = dict()
for w in all_strings(n, s, t):
  m = lut[maximum_rotation(w, n)]
  orbs.setdefault(m, []).append(lut[ternary_value(w)])

print()
print('Orbits')
for k, v in orbs.items():
  print(' ', k,':', v)

print()
print('Orbit sizes vs. count')
orbit_size_lut = Counter(map(len, orbs.values()))
for orb_size, count  in sorted(orbit_size_lut.items()):
  print(' ', orb_size, ':', count)

print('Aperiodics:', aperiodic(n, s, t), orbit_size_lut[n])
print('Number of orbits:', len(orbs))
print('Burnside orbit count:', burnside(n, s, t))
print('Checksum:', sum(map(len, orbs.values())))