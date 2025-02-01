# Let's sanity check out Burnside stuff brute'ly

from math import factorial as fac

def nCr(n, k):
  return fac(n) // fac(k) // fac(n-k)

def all_strings(n, s, t):
  if n:
    if s:
      for pot in all_strings(n-1, s-1, t):
        yield [0] + pot
    if t:
      for pot in all_strings(n-1, s, t-1):
        yield [2] + pot
    for pot in all_strings(n-1, s, t):
      yield [1] + pot
  elif s == 0 and t == 0:
    yield []

def ternary_value(w):
  ret = 0
  for e in w:
    ret *= 3
    ret += e
  return ret

def from_ternary(x, n):
  ret = []
  for _ in range(n):
    ret.append(x%3)
    x //= 3
  return ret

def maximum_rotation(w, n):
  ret = 0
  for x in range(n):
    pot = ternary_value(w)
    if pot > ret:
      ret = pot
    w = w[1:] + [w[0]]
  return ret

def all_orbits(n, s, t):
  orbits = dict()
  for w in all_strings(n, s, t):
    m = tuple(from_ternary(maximum_rotation(w, n), n))
    orbits[m] = orbits.get(m, 0) + 1
  return orbits

def string_count(n, s, t):
  return nCr(n, s) * nCr(n-s, t)

# how many orbits of (n,s,t)-strings
def burnside(n, s, t):
  ret = 0
  for d in range(1, n+1):
    if n%d != 0 or s%d != 0 or t%d != 0:
      continue
    ret += string_count(n//d, s//d, t//d)
  return ret // n

# how many orbits of (n,s,t)-strings with order n
def aperiodic(n, s, t):
  # hail mary
  ret = 0
  for d in range(1, n+1):
    if n%d != 0 or s%d != 0 or t%d != 0:
      continue
    ret += mobius_function(d) * string_count(n//d, s//d, t//d)
  return ret // n

def mobius_function(n):
  if n == 1:
    return 1

  k = 0  # number of distinct prime factors
  for d in range(2, n+1):
    if n%d == 0 and is_prime(d):
      if n//d % d == 0:
        return 0  # not squarefree
      k += 1
  return pow(-1, k)

def is_prime(n):
  if n < 4:
    return n > 1
  if n%2 == 0 or n%3 == 0:
    return False
  for x in range(5, 2 + int(n**.5), 6):
    if n%x == 0 or n%(x+2) == 0:
      return False
  return True
