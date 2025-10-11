
'''
Hal hand-jams some PAs together into a higher n
'''


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret

def distance(u, v):
  return max(abs(a-b) for a,b in zip(u,v))


def block_distance(A):
  d = float('inf')
  for ux in range(len(A)):
    for vx in range(ux):
      d = min(d, distance(A[ux], A[vx]))
  return d


def do_shift(row, f):
  return [f.get(e, e) for e in row]


def make_first_component():
  # define the mapping
  f = dict()
  for x in range(4):
    # f[(x+1) % 4] = x
    f[(x+1) % 4] = x

  # load the PA
  Z = load_pa('../results/pa_6_choose_3_verified.txt')
  A = []
  for row in Z:
    A.append([0] + [e+1 for e in row] + [7])

  # B = []
  # for row in A:
  #   B.append(do_shift(row, f))
  # A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


def make_second_component():
  # define the mapping
  f = {i:e for i,e in enumerate([1,2,4,5])}

  # load the PA
  Z = load_pa('../results/pa_4_choose_2_verified.txt')
  A = []
  for row in Z:
    A.append([0] + do_shift(row, f) + [6,7,3])

  # # define the mapping
  # f = dict()
  # for x in range(3):
  #   f[(x+1) % 3] = x

  # B = []
  # for row in A:
  #   B.append(do_shift(row, f))
  # A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


def make_third_component():
  # define the mapping
  f = {i:(e-1) for i,e in enumerate([3, 4, 6, 7])}

  # load the PA
  Z = load_pa('../results/pa_4_choose_2_verified.txt')
  A = []
  for row in Z:
    A.append([4,0,1] + do_shift(row, f) + [7])

  # # define the mapping
  # f = dict()
  # r = range(1,4)
  # for i,e in enumerate(r):
  #   f[e] = r[(i-1)%len(r)]

  # B = []
  # for row in A:
  #   B.append(do_shift(row, f))
  # A=B

  # f = dict()
  # r = [7, 4, 3]
  # for i,e in enumerate(r):
  #   f[e] = r[(i-1)%len(r)]

  # B = []
  # for row in A:
  #   B.append(do_shift(row, f))
  # A=B

  # print the PA
  for x,row in enumerate(A):
    print (x, [e for e in row])

  # print the distance
  d = block_distance(A)
  print('Distance is:', d)


make_first_component()
make_third_component()
make_second_component()



'''
5 1 2 3 4 6 7 8
5 1 2 3 6 4 7 8
5 1 2 3 6 7 4 8
5 1 2 6 3 4 7 8
5 1 2 6 3 7 4 8
5 1 2 6 7 3 4 8
'''


