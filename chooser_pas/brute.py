# create a fully filled Chebyshev PA by brute force

from collections import Counter


def pots(pa, n, d):
  def recur(sofar, subpa, remaining):
    if len(sofar) >= n and not subpa:
      yield sofar
      return

    for x in remaining:
      nexpa = []
      for e in subpa:
        if abs(x - e[len(sofar)]) < d:
          nexpa.append(e)
      yield from recur(sofar+[x], nexpa, remaining-{x})

  yield from recur([], pa, set(range(n)))


def pots2(pa, n, d):
  def recur(sofar, subpa, remaining):
    if len(sofar) >= n:
      if not subpa:
        yield sofar
      return

    # c[x] is the number of rows that x would fail to disagree with
    c = [0]*n
    for e in subpa:
      y = e[len(sofar)]
      for x in range(max(0, y-d+1), min(n, y+d-1)):
        c[x] += 1

    c = sorted([(e,x) for x,e in enumerate(c) if x in remaining])

    for _,x in c:
      nexpa = []
      for e in subpa:
        if abs(x - e[len(sofar)]) < d:
          nexpa.append(e)
      yield from recur(sofar+[x], nexpa, remaining-{x})

  yield from recur([], pa, set(range(n)))


def brute(pa, n, d, cutoff=None):
  def recur(adjunct):
    for e in pots2(pa+adjunct, n, d):
      yield from recur(adjunct+[e])
    yield adjunct

  ret = []
  for e in recur([]):
    if len(e) > len(ret):
      ret = e
      print()
      print('-'*10)
      for row in ret:
        print(row)
      print("new best:", len(ret))
      if cutoff is not None and len(ret) >= cutoff:
        return ret
  return ret

# pa = brute([], 6, 3, 20)
pa = brute([], 10, 5, 252)
for row in pa:
  print(row)
print(len(pa))