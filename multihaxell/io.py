import os


def resume_computation(filename):
  good = False
  M = dict()
  if os.path.exists(filename):
    print(f'Found a file named {filename}')
    with open(filename, 'r') as f:
      good = True
      for line in f:
        # clean up the line
        line = line.split('#')[0].strip()
        if len(line) == 0:
          continue

        # get the permutation out of it
        row = tuple(map(int, line.split()))
        color = get_color(row)
        M[color] = row

        # validate the permutation
        if len(row) != perm_len:
          good = False
          print(f'One row has {len(row)} permutations. We are not using this file.')
          if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
            exit(0)

      # verify the partial independent transversal
      P = list(M.values())
      for ux,u in enumerate(P):
        for vx in range(ux):
          v = P[vx]
          if edge(u,v):
            good = False
            print('Not separated 1:', u)
            print('Not separated 2:', v)
            print(f'Two rows are not separated. We are not using this file.')
            if 'y' != input('Overwrite this file and continue from scratch? (y/N)').lower():
              exit(0)

  if good:
    return M
  return dict()
