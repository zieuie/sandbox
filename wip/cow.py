

from functools import cache

# p = 3
# k = 1
# q = p**(2*k+1)

def best(p, k):
  @cache
  def recur(a, positions, symbols, history):
    if positions <= 0 or symbols <= 0:
      return a, history
    
    pota = recur(a+1, positions - p**k, symbols - p**(k+1), tuple())
    potb = recur(a+1, positions - p**(k+1), symbols - p**k, tuple())
    if pota[0] >= potb[0]:
      return pota[0], pota[1] + ((p**k, p**(k+1)),)
    else:
      return potb[0], potb[1] + ((p**(k+1), p**k),)

  q = p**(2*k+1)
  return recur(0, q, q, tuple())

# def main():

print(best(3, 1))