# Let's try Zevi's thing
# Where you make a PA of length 2k
# Where each row has one zero and k 2s and k-1 1s
# But is fully separated

# WLOG, let row x have its 0 in column x
# Then each row only need be an index an a set of 2s.
# Since columns permute, let the first row have its 2s in column 1 to k.

# Then on each recursive trial, we have to construct only those rows
# that are separated from previous rows.
# That's some kind of covering, right? Hm.

# Let's be naive first and always assign all 2s.


import itertools as it


def hydrate(k, r, twos):
  ret = [1]*(2*k)
  ret[r] = 0
  for t in twos:
    ret[t] = 2
  return ret


# yield all k-length assignments of 2s places that cover the pa, given that r alone is 0
def coverers(k, r, pa):
  def recur(sofar, cur):
    # sofar is my twos so far
    # cur is the index of the row I'm trying to appease
    if cur >= len(pa):
      # we did it boys
      yield sofar
    elif cur in sofar or r in pa[cur]:
      # we already cover this dude
      yield from recur(sofar, cur+1)
    elif len(sofar) < k:
      # we need a new 2 to cover this
      yield from recur(sofar + (cur,), cur+1)

  for partial in recur(tuple(), 0):
    # the partial is full length
    if len(partial) == k:
      yield partial
      continue

    # we have some choices
    for tt in it.combinations(list(set(range(2*k)) - set(partial) - {r}), k-len(partial)):
      yield sorted(partial+tt)

def separations(u, v):
  ret = 0
  for a,b in zip(u,v):
    if (a,b) in ((0,2), (2,0)):
      ret += 1
  return ret

def main(k):
  # given the first few rows of the pa, return all full pas that are separated
  def recur(sofar):
    # we're done!
    if len(sofar) >= 2*k:
      yield sofar
      return

    # recurse
    for pot in coverers(k, len(sofar), sofar):
      yield from recur(sofar + [pot])

  for idx, pot in enumerate(recur([list(range(1, k+1))])):
    if idx:
      continue
    print()
    print('-'*10)
    pa = []
    for r, twos in enumerate(pot):
      row = hydrate(k, r, twos)
      pa.append(row)

    disagreements = []
    for ux, u in enumerate(pa):
      row = []
      for vx, v in enumerate(pa):
        row.append(separations(u, v))
      disagreements.append(row)

    for x, (row, dis) in enumerate(zip(pa, disagreements)):
      print (x, row, dis)

    # break
  print(idx+1)

from sys import argv
main(int(argv[1]))