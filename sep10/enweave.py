import random
import itertools as it
import re
from sys import argv


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def enweave(A, n, d):
  ret = []
  lows = list(range(d))
  for row in A:
    for ps in it.combinations(list(range(n)), d):
      random.shuffle(lows)
      l, h = 0, 0
      new = []
      for i in range(n):
        if i in ps:
          new.append(lows[l])
          l += 1
        else:
          new.append(row[h]+d)
          h += 1
      ret.append(new)
  return ret


infile = argv[1]
A = load_pa(infile)
n,d = len(A[0]), int(re.findall(r'\d+', infile)[1])
T = [[e//d for e in t] for t in A]
