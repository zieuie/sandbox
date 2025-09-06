
COLORS = [
  '\033[91m', # red
  '\033[92m', # green
  '\033[93m', # yellow
  '\033[94m',
  '\033[95m',
  '\033[96m',
  '\033[97m',
]

# COLORS = [
#   '\033[41m',
#   '\033[42m',
#   '\033[43m',
#   '\033[44m',
#   '\033[45m',
#   '\033[46m',
#   '\033[47m',
# ]

NC = '\033[0m'

def color(s, x):
  return COLORS[x] + s + NC

def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


# n,d = 6,3
# n,d = 9,3
# A = load_pa(f'../results/pa_{n}_choose_{d}_verified.txt')
# A = load_pa(f'pa_{n}_choose_{d}_verified.txt')

n,d = 8,3
A = load_pa(f'sand/pa_{n}_choose_{d}_unfinished.txt')

# Q[r][c] = [v]

from collections import defaultdict

Q = defaultdict(lambda: defaultdict(set))
for vx, v in enumerate(A):
  for ux in range(vx):
    for c, (a,b) in enumerate(zip(A[ux], v)):
      # if abs(a-b) >= d:
      if abs(a-b) >= d and (abs(a//d - b//d) <= 1):
        Q[ux][c].add(vx)
        Q[vx][c].add(ux)

for r in range(len(A)):
  print('%4s |' % r, end='')
  cv = Q[r]
  for c in range(n):
    vs = cv.get(c, tuple())
    # print(color('%4s' % len(vs), A[r][c]%d), end=' ')
    print(color('%4s' % len(vs), A[r][c]//d), end=' ')
  print()


