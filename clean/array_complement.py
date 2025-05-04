import itertools as it
from lib import *

def complement_pa(n,A):
  for row in A:
    yield [n-e-1 for e in row]


def main():
  from sys import argv
  filename = argv[1]
  A = load_pa(filename)
  n = len(A[0])
  with open(f'complemented_{filename}', 'w+') as f:
    for row in complement_pa(n, A):
      f.write(' '.join(map(str,row)))
      f.write('\n')


if __name__ == '__main__':
    main()

