# Let's sanity check out Burnside stuff brute'ly

from collections import Counter
from lib import string_count, all_strings, all_orbits, aperiodic, burnside

## Configure
# n, s, t = 10, 2, 4
# n, s, t = 5, 2, 1
n, s, t = 12, 4, 4
# n, s, t = 30, 2, 2
# n, s, t = 6, 3, 3
# n, s, t = 6, 4, 2
print('Calculated strings:', string_count(n, s, t))

## Print all strings
every_string = all_strings(n, s, t)
# for x, w in enumerate(every_string):
#   print(x, w)
# string_count = x+1
len_every_string = sum(1 for _ in every_string)

print('Generated strings: ', len_every_string)

orbs = all_orbits(n, s, t)

# print()
# print('Orbits')
# for k, v in orbs.items():
#   print(' ', k,':', v)

print()
print('Orbit sizes vs. count')
orbit_size_lut = Counter(orbs.values())
for orb_size, count  in sorted(orbit_size_lut.items()):
  print(' ', orb_size, ':', count)

print('Aperiodics:', aperiodic(n, s, t), orbit_size_lut[n])
print('Number of orbits:', len(orbs))
print('Burnside orbit count:', burnside(n, s, t))
print('Checksum:', sum(orbs.values()))