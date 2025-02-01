# Trying to understand Cattell

def main(n, k):
  
  def recur(t):
    if t == 1:
      for e in range(k):
        yield ((e,), 1)
      return
    
    for a, p in recur(t-1):
      # print(t, a, p)
      yield (a+(a[t-p-1],), p)
      for b in range(a[t-p-1]+1, k):
        yield (a+(b,), t)
    
  for a, p in recur(n):
    print(a, p)

def cattell(n, k):
  a = [0]*(n+1)
  def gen(t, p):
    if t > n:
      # if t % p == 0:
        yield a[1:], p
    else:
      a[t] = a[t-p]
      yield from gen(t+1, p)
      for j in range(a[t-p]+1, k):
        a[t] = j
        yield from gen(t+1, t)

  for e, p in gen(1, 1):
    print(e, '*' if 0 == len(e) % p else '')


def ruskey(n, k, d):
  # positions of nonzero chars
  a = [0]*(d+1)

  # values of nonzero chars
  b = [0]*(d+1)
  
  # t is density so far, p is lyndon length
  def genfix(t, p):
    # print('>'*t, p, a)
    # base case
    if t >= d-1:
      yield a[1:], b[1:], p
      return

    tail = n - (d-t) + 1  # maximal position of next char
    max = a[t+1-p] + a[p]
    # print(' '*t, p, tail, max, a[t+1-p], a[p], a)
    if max <= tail:
      a[t+1] = max
      b[t+1] = b[t+1-p]
      yield from genfix(t+1, p)
      for i in range(b[t+1]+1, k):
        b[t+1] = i
        yield from genfix(t+1, t+1)
      tail = max-1
    for j in range(tail, a[t], -1):
      a[t+1] = j
      for i in range(1, k):
        b[t+1] = i
        yield from genfix(t+1, t+1)

  for a, b, _ in genfix(1, 1):
    # print('.'*d, pot)
    pot = [0]*n
    for p,q in zip(a,b):
      pot[p] = q

    for q in range(1, k):
      pot[-1] = q
      # print(pot)
      yield pot



'''
Let's write down some of their useful knowledge.

From Gen2
 - For every necklace of positive density, the last character of the string must be nonzero
 - We compute the maximal position for the next character using the following expression:
    ( (t+1) // p ) * a[p] + (a[t+1] % p)
 - The minimal value for this position it b[t+1-p]
 - All larger values at the maximal position are also valid
 - All positions before the maximum position and greater than the position of the last assigned nonzero character a[t] can hold all vlaues ranging from 1 to k-1. 

From GenFix
  - No necklaces can have the first nonzero character in a position after n-d+1 or before floor((n-1)/d + 1)
  - Having placed the ith nonzero, the i+1st nonzero must come before n-(d-i)+2.
  - The last nonzero must be in the nth position, so we don't generate the d-1st nonzero.
  - There's a trick to choosing the last nonzero, which is:
    > 

'''
def fix(n, k, d):
  # screw it, let's make our own.
  # n = length of str
  # k = size of alphabet
  # d = density of nonzero beads

  def recur(p, a, b):
    # t is the number of nonzero symbols placed
    # a is the array of positions of symbols
    # b is the array of values of symbols corresponding to a
    if len(a) >= d-1:
      # we're done
      yield p, a, b
      return

    start = a[-1]+1 if a else (n-1)//d + 1 
    for position in range(start, n-d+len(a)+1):
      for value in range(1, k):
        yield from recur(1, a+(position,), b+(value,))

  for p, a, b in recur(1, tuple(), tuple()):
    pot = [0]*n
    for p,q in zip(a,b):
      pot[p] = q

    for q in range(1, k):
      pot[-1] = q
      # print(pot)
      yield pot


if __name__ == '__main__':
  from sys import argv
  for pot in fix(int(argv[1]), int(argv[2]), int(argv[3])):
    print(pot)
