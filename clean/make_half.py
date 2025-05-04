import itertools as it
import random


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def weave(lows, highs):
  ret = []
  n = len(lows) + len(highs)
  d = len(highs)
  for ps in it.combinations(list(range(n)), d):
    random.shuffle(lows)
    random.shuffle(highs)
    l, h = 0, 0
    new = []
    for i in range(n):
      if i in ps:
        new.append(highs[h])
        h += 1
      else:
        new.append(lows[l])
        l += 1
    ret.append(new)
  return ret


def main():
  from sys import argv
  filename = argv[1]
  A = load_pa(filename)
  n = len(A[0])
  h = n//2 + 1
  H = []
  for row in A:
    pot = [h,0]
    for e in row:
      if e+1 < h:
        pot.append(e+1)
      else:
        pot.append(e+2)
    H.append(pot)

  for row in weave(list(range(0, h)), list(range(h+2, n+2))):
    H.append([h, h+1] + row)

  with open('half.txt', 'w+') as f:
    for row in H:
      f.write(' '.join(map(str,row)))
      f.write('\n')


if __name__ == '__main__':
    main()

