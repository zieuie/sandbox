from gurobipy import Model, GRB
import numpy as np


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def grumble(row):
  s = max(row)
  s = [0]*(s+1)
  for e in row:
    s[e] += 1
  ret = [0]
  for e in s:
    ret.append(ret[-1]+e)
  return ret


def fill_constraints(model, x, n, d, grid):
  prefixes = grumble(grid[0])
  print("prefixes", prefixes)
  # Constraint: Each number appears once per row
  for r in range(len(grid)):
    for c in range(n):
      e = grid[r][c]
      model.addConstr(sum(x[r, c, q] for q in range(prefixes[e], prefixes[e+1])) == 1)



########

def extract_result(model, n, N, x):
  if model.status == GRB.OPTIMAL:
    solution = np.zeros((N, n), dtype=int)
    for r in range(N):
      for c in range(n):
        for q in range(n):
          if x[r, c, q].x > 0.5:
            solution[r, c] = q
    return solution
  else:
    return None


def basic_constraints(model, x, n, d, N):
  # Constraint: Each cell must have exactly one number
  for r in range(N):
    for c in range(n):
      model.addConstr(sum(x[r, c, q] for q in range(n)) == 1)

  # Constraint: Each number appears once per row
  for r in range(N):
    for q in range(n):
      model.addConstr(sum(x[r, c, q] for c in range(n)) == 1)

  # Constraint: Every pair has Chebyshev distance >= d
  for v in range(N):
    for u in range(v):
      asdf = []
      for c in range(n):
        for q in range(n):
          row = []
          for k in range(n):
            if abs(q-k) >= d:
              row.append( x[v,c,k] )
          asdf.append(x[u,c,q] * sum(row))
      model.addConstr(sum(asdf) >= 1)


def solve_pa(n, d, grid):
  N = len(grid)
  model = Model("Chebyshev")
  x = model.addVars(N, n, n, vtype=GRB.BINARY, name="x")
  basic_constraints(model, x, n, d, N)
  fill_constraints(model, x, n, d, grid)
  model.optimize()
  return extract_result(model, n, N, x)


if __name__ == '__main__':
  from sys import argv
  d, infile = int(argv[1]), argv[2]
  grid = load_pa(infile)
  N = len(grid)
  n = len(grid[0])
  ret = solve_pa(n, d, grid)
  outfile = f'pa_{n}_{d}_{N}.txt'
  with open(outfile, 'w+') as f:
    for row in ret:
      # print(row)
      f.write(str(row) + '\n')
  print()
  print('Wrote to', outfile)
