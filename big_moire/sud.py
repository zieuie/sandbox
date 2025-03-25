

import itertools as it

def distance(s, t):
    return max(abs(a-b) for a,b in zip(s,t))

# A = list(it.permutations(list(range(1, 6))))
# N = len(A)
# ret = []
# for u in range(N):
#     for v in range(u):
#         if sorted(i for i,e in enumerate(A[u]) if e < 4) == sorted(i for i,e in enumerate(A[v]) if e < 4):
#             continue

#         if distance(A[u],A[v]) <= 2:
#             ret.append((u,v))



# for a,b in ret:
#     print()
#     print(distance(A[a], A[b]))
#     print(A[a])
#     print(A[b])

# print(len(ret))

def weave(n, hi, ls, hs):
    ret = []
    l, h = 0, 0
    for i in range(n):
        if i in hi:
            ret.append(hs[h])
            h += 1
        else:
            ret.append(ls[l])
            l += 1
    return ret


# make a dual-pa. One where pairs have distance < d
def brute(n, d):
    H = list(it.combinations(list(range(n)), d))
    lows = list(range(1,n-d+1))
    highs = list(range(n-d+1, n+1))
    def recur(sofar, i):
        if i >= len(H):
            yield sofar
            return
        for ls in it.permutations(lows):
            for hs in it.permutations(highs):
                t = weave(n, H[i], ls, hs)
                for x in sofar:
                    if distance(x, t) > d:
                        break
                else:
                    yield from recur(sofar + [t], i+1)

    for A in recur([], 0):
        print('-'*10)
        for row in A:
            print (row)
        input()

# brute(5, 3)

from lib import *

