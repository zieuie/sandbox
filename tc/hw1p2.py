# from collections import defaultdict

A = {'ab', 'c', 'xy'}
f = lambda x: x in A


# for l > 0
# T[l][u] is:
#  - True if x[u:u+l] in A
#  - True if T[w][u] and T[l-w][u+w] in A (for any w in 1 to l-1)


def is_in_A_kleene(x):
  L = len(x)
  T = []
  for u in range(L):
    T.append([False for l in range(L-u+1)])

  for l in range(1, L+1):
    for u in range(L-l+1):
      if f(x[u:u+l]):
        T[u][l] = True
      else:
        for w in range(1, l):
          if T[u][w] and T[u+w][l-w]:
            T[u][l] = True

  return T[0][L]


print(is_in_A_kleene('ls'))
print(is_in_A_kleene('a'))
print(is_in_A_kleene('ab'))
print(is_in_A_kleene('ababab'))
print(is_in_A_kleene('ababcab'))
print(is_in_A_kleene('ababcabxyab'))