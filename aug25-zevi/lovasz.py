
# This is Zevi's Corollary 3

from functools import cache
# from math import factorial, e as E
from math import factorial

from decimal import Decimal, getcontext, ROUND_HALF_UP
getcontext().rounding = ROUND_HALF_UP
getcontext().prec = 1000

D = Decimal

# Calculate e using its series expansion
E = Decimal(0)
fact = Decimal(1)
for i in range(getcontext().prec*100):
  E += Decimal(1) / fact
  fact *= Decimal(i + 1)


@cache
def binom1(n,k):
  if n == 0 or n == k:
    return 1
  else:
    return binom1(n-1,k-1) + binom1(n-1,k)


def binom2(n,k):
  f = factorial
  return f(n) // f(k) // f(n-k)


WE = D(2*E)
def naive(n,k):
  b = binom2
  num = b(n, 2*k) * b(2*k, k)
  den = 0
  for t in range(0, k+1):
    for i in range(0, k+1):
      den += b(k,t) * b(k,i) * b(n-2*k, 2*k-i-t) * b(2*k-i-t, k-i)

  c = D(num)/D(den)
  # print('n,k', n,k)
  # print('num: ', num)
  # print('den: ', den)
  # print('frac:', c)
  # print(f'(k={k}) P({n}, {n-2*k+1}) >= {(c-1)/WE}')
  # print(f'(k={k}) P({n}, {n-2*k+1}) >= {(c-1)/WE}')
  print(f'{k},{n},{n-2*k+1},{round(10000*(c-1)/WE)/10000}')
  pass


# n6 k2 -> too small 1/2e * 90/26
# n10 k3 -> too small 1/2e * 4200/471
# get up to 30-60 if you can and k various values at most n/4.

# naive(8,2)
# naive(60,15)
# naive(80,20)
# naive(400,100)

# for k in range(10,101,10):
#   # for n in range(k*4, k*6, 10):
  # for n in range(k*4, 1001, 10):
#     naive(n,k)


for n in range(20, 501, 10):
  # for k in range(0, n//4+1):
    naive(n,n//4)



# print (1/WE)