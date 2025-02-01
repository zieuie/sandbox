# Let's try Cattell's CAT algorithm for generating Lyndon words

from collections import Counter
from lib import string_count, all_strings, all_orbits, aperiodic, burnside



# Python implementation of
# the above approach
def lyndon(n, k):
  # To store the indices
  # of the characters
  w = [-1]

  # Loop till w is not empty
  while w:
    # Incrementing the last character
    w[-1] += 1
    m = len(w)
    if m == n:
      yield w

    # Repeating w to get a
    # n-length string
    w = w*(1 + n//m)

    # Removing the last character
    # as long it is equal to
    # the largest character in S
    i = n-1
    while i >= 0 and w[i] == k - 1:
      i -= 1
    w = w[:i+1]



def cattell(n, k):
  a = [0]*n
  def gen(t, p):
    print(f'gen({t}, {p})')
    # base case
    if t > n:
      # prenecklaces P_k(n)
      # print(a)

      # lyndon_words L_k(n)
      # if p == n:
      #   yield a

      # # necklaces N_k(n)
      if n % p == 0:
        yield a
      
      # # de brujin
      # if n % p == 0:
      #   print(a[:p])

      return

    # recursive case?
    a[t] = a[t-p]
    gen(t+1, p)
    for j in range(a[t-p]+1, k):
      a[t] = j
      gen(t+1, t)  

  # yield from gen(1, 1)
  yield from gen(0, 0)


def meep(n, k):
  # a, lyn(a)
  A = [((0,), 1)]
  for t in range(2, n+1):
    nex = []
    for a, p in A:
      anp = a[t-p-1]
      nex.append((a+(anp,), p))
      for b in range(anp+1, k):
        nex.append((a+(b,), t))
    A, nex = nex, []

for e in cattell(5, 2):
  print(e)







exit(0)


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