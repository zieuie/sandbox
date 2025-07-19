
from lib import load_pa, dump_pa
# from sys import argv

def complement(p, n):
  return [n-1-e for e in p]

infile = 'sergey.txt'
outfile = 'out.txt'
n = 9

A = load_pa(infile)
for x in range(len(A)):
  A[x] = complement(A[x], n)
dump_pa(A, outfile)
