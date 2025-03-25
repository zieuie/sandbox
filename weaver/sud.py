

import itertools as it

def distance(s, t):
    return max(abs(a-b) for a,b in zip(s,t))

A = list(it.permutations(list(range(1, 6))))

A = list(filter(lambda s: s.index(1) < s.index(2) < s.index(3), A))

N = len(A)
ret = []
for u in range(N):
    for v in range(u):
        if sorted(i for i,e in enumerate(A[u]) if e < 4) == sorted(i for i,e in enumerate(A[v]) if e < 4):
            continue

        if distance(A[u],A[v]) <= 2:
            ret.append((u,v))



for a,b in ret:
    print()
    print(distance(A[a], A[b]))
    print(A[a])
    print(A[b])

print(len(ret))


