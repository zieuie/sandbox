

def main(n):
  d = n//2

  A = make_array(n,d)
  C = make_components(A,d)
  F = make_fixes(C,A,n)

  one = dict()

  for ccid,(pre,suf) in F.items():
    mid = sorted(set(range(n)) - set(pre) - set(suf))
    vs = C[ccid]
    # if len (vs) == 1:
    #   continue
    one[ccid] = mid

  Z = []
  for ccid, map in one.items():
    vs = C[ccid]
    m = len(map)
    # print()
    # print(f'CCID {ccid} length {len(vs)} m = {m}')
    M = []
    pre, suf = F[ccid]

    if m:
      Z.append(pre + [-1]*m + suf)
    #   for v in load_pa(f'../results/pa_{m}_choose_{m//2}_verified.txt'):
    #     row = pre + do_shift(v, map) + suf
    #     M.append(row)
    #     print(row)
    else:
      Z.append(pre)
    #   row = pre
    #   M.append(row)
    #   print(row)
    # print(block_distance(M))

  # for row in Z:
  #   print(row)    

  lut = [defaultdict(set) for _ in range(len(Z))]
  # lut2 = [set(range(n)) for _ in range(len(Z))]
  dd = d*d
  for vx in range(len(Z)):
    for ux in range(vx):
      is_separated = False
      # separations = []
      # if not separated2(Z[ux], Z[vx], 4):
      for x, (a,b) in enumerate(zip(Z[ux], Z[vx])):
        if a>=0 and b>=0 and (a-b)**2 >= dd:
          # print('se')
          is_separated = True
          # separations.append(x)
          # lut2[ux].discard(a)
          # lut2[vx].discard(b)
          lut[ux][x].add(vx)
          lut[vx][x].add(ux)
      # return False
      if not is_separated:
        print('not separated', ux, vx)
  print('separated')

  for x, row in enumerate(Z):
    if -1 in row:
      # print (row, {k: len(v) for k,v in lut[x].items()})
      # print (row, sorted(lut2[x]))
      print (row)
      for k,v in sorted(lut[x].items()):
        print ('   ', k, sorted(v))

  # print()

  # newz = hill_climb_driver(Z,n,d)
  # for row in newz:
  #   print(row)

main(8)

