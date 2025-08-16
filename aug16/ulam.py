
import itertools as it
from functools import cache
from time import time
import sys
sys.setrecursionlimit(200000)

# https://www.geeksforgeeks.org/dsa/longest-common-subsequence-dp-4/
@cache
def lcs(S1, S2):
    m = len(S1)
    n = len(S2)

    # Initializing a matrix of size (m+1)*(n+1)
    dp = [[0] * (n + 1) for x in range(m + 1)]

    # Building dp[m+1][n+1] in bottom-up fashion
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if S1[i - 1] == S2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j],
                               dp[i][j - 1])

    # dp[m][n] contains length of LCS for S1[0..m-1]
    # and S2[0..n-1]
    return dp[m][n]


def brute(n, d):
  perms = list(map(tuple, it.permutations(list(range(n)))))
  sofar = []
  best = []
  def recur(i=0):
    global START_TIME
    global MAX_TIME
    global TIMED_OUT
    nonlocal best

    if time() - START_TIME > MAX_TIME:
      TIMED_OUT = True
      return

    if i >= len(perms):
      if len(sofar) > len(best):
         best = sofar
      return

    u = perms[i]
    for v in sofar:
      if n-lcs(u,v) < d:
         break
    else:
       sofar.append(u)
       recur(i+1)
       sofar.pop()

    recur(i+1)

  recur()
  return best


MAX_TIME = 2
for n in range(4, 10):
  for d in range(1, n+1):
    try:
      START_TIME = time()
      TIMED_OUT = False
      A = brute(n, d)
      if not TIMED_OUT:
        print(f'U({n}, {d}) == {len(A)}')
      else:
        print(f'U({n}, {d}) >= {len(A)}')
    except KeyboardInterrupt:
      exit(0)
