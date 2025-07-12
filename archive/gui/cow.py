
from lib import *
from copy import deepcopy


# create an n-string from src, inserting symbols qs in positions ps
def infill(n, src, ps, qs):
  h,l = 0,0
  ret = []
  for i in range(n):
    if i in ps:
      ret.append(qs[h])
      h += 1
    else:
      ret.append(src[l])
      l += 1
  return ret


# generate all (n choose d) PAs with a fixed first row.
def make_seeds(n, d):
  ps = list(it.combinations(list(range(n)), d))
  sofar = [infill(n, list(range(n-d)), ps[0], list(range(n-d, n)))]
  # sofar = []
  def recur(i):
    if i >= len(ps):
      # yield sofar
      yield deepcopy(sofar)
      return

    for lows in it.permutations(list(range(n-d))):
      for highs in it.permutations(list(range(n-d, n))):
        pot = infill(n, lows, ps[i], highs)
        for r in sofar:
          if not separated(pot, r, d):
            break
        else:
          sofar.append(pot)
          yield from recur(i+1)
          sofar.pop()

  # yield from recur(0)
  yield from recur(1)


# j: ^^^..xxx
# i: xxx..^^^


# j: ^^^xxx..
# i: ^^x^xx..
# Find j with most carats in common with i
def find_high_overlap(ps, i):
  overlap = set()
  best = None
  for j in range(i):
    pot = ps[i] & ps[j]
    if len(pot) > len(overlap):
      overlap, best = pot, j

  if best is not None:
    return best


# n=8, d=3
# 0 1 2 3 4 5 6 7
# . . x x x ^ ^ ^

def select(n, d, sofar, i, j, xj):
  chunk_size = len(sofar) // i
  for idx in range(chunk_size*j, chunk_size*(j+1)):
    row = sofar[idx]
    for x in xj:
      if not (n-d-d <= row[x] < n-d):
        break
    else:
      return idx, row

  print ('What the heck?')
  print ('Failed to select', n, d, i, j, xj)
  for idx in range(chunk_size*j, chunk_size*(j+1)):
    print ('  -', sofar[idx])
  return None, None


def find_compatible(n, d, sofar, ps, i, j):
  # pi, pj is where the ^ symbols are
  pi, pj = ps[i], ps[j]
  overlap = pj ^ pi
  common = sorted(set(range(n)) - pi - pj)

  # xi, xj is where the x symbols are
  xi0 = overlap - pi
  xi = xi0 | set(common[:d-len(xi0)])

  xj0 = overlap - pj
  xj = xj0 | set(common[:d-len(xj0)])

  # rj is the row in where ^ is in pj and x is in xj
  _, rj = select(n, d, sofar, i, j, xj)
  if rj is None:
    print()

  pi, xi = sorted(pi), sorted(xi)
  ap = [ rj[x] for x in sorted(pi) ]
  ax = [ rj[x] for x in sorted(xi) ]

  for bp in it.permutations(list(range(n-d, n))):
    for bx in it.permutations(list(range(n-d-d, n-d))):
      if separated(ap+ax, bp+bx, d):
        yield pi, bp, xi, bx


# brute all the ways to add one more permutation with its highs in the ps[i] positions
# return j, src, dst such that apply_permutation(seeds[j][_], src, dst) is separated from sofar

def find_separated_seeds(n, d, seeds, sofar, pi, bp, xi, bx):
  # try every seed
  seed_order = list(range(len(seeds)))
  random.shuffle(seed_order)
  for x in seed_order:
    seed = seeds[x]
    # see if every row in the seed works
    good = True
    ret = []
    for row in seed:
      # permute the row
      um = infill(n, row, pi, bp)
      # u = apply_permutation(um, xi, bx)
      u = um[:]
      for p,q in zip(xi,bx):
        u[p] = q

      # print ('u =', u, 'um =', um, 'row =', row, 'pi =', pi, 'bp =', bp, 'xi =', xi, 'bx =', bx)
      ret.append(u)

      # see if the row is separated from all the sofar
      for v in sofar:
        if not separated(u, v, d):
          good = False
          break

      if not good:
        break

    # this seed is good
    if good:
      yield x, ret
      # vps.append((pi, bp, xi, bx))


def brute(n, d):
  seeds = list(make_seeds(n-d, d))
  print('Seeds', len(seeds))
  # ps = list(map(set, it.combinations(list(range(n)), d)))
  ps = list(map(set, kitty_combos(n, d)))
  print('ps', len(ps))

  sofar = []
  state = []
  def recur(i):
    # print ('>'*i, 'Recur', i)
    # we did it
    if i >= len(ps):
      yield deepcopy(sofar)
      return

    # first run
    if i == 0:
      for seed_no, seed in enumerate(seeds):
        state.append((seed_no,))

        qs = list(range(n-d,n))
        for row in seed:
          sofar.append(infill(n, row, ps[i], qs))
        yield from recur(1)
        for _ in seed:
          sofar.pop()

        state.pop()
        break  # only do the first seed
      return

    # find a ps of high overlap
    j = find_high_overlap(ps, i)
    # print ('>'*i, 'High overlap', j, ps[j], i, ps[i])

    # find the compatible mids
    compatible = list(find_compatible(n, d, sofar, ps, i, j))
    print ('>'*i, 'Recur', i, 'Compatible', len(compatible), j, ps[j], i, ps[i], state)
    # print ('ps', ps[:i])
    # print ('>'*i, 'Compatible', len(compatible))
    # for pi, bp, xi, bx in compatible:
    #   print ('>'*i, ' -', pi, bp, xi, bx)

    # filter out the ones that aren't fully separated
    for pi, bp, xi, bx in compatible:
      for seed_no, mute in find_separated_seeds(n, d, seeds, sofar, pi, bp, xi, bx):
        state.append((seed_no,))
        for row in mute:
          sofar.append(row)
        yield from recur(i+1)
        for row in mute:
          sofar.pop()
        state.pop()

  yield from recur(0)


# n=8, d=3
# 0 1 2 3 4 5 6 7
# . . x x x ^ ^ ^
def deduce_mutation(n, d, src, dst):
  msrc, mdst, hp, hq = [], [], [], []
  i = 0
  for p,q in enumerate(dst):
    if q >= n-d:
      hp.append(p)
      hq.append(q)
    elif q >= n-d-d:
      msrc.append(src.index(q))
      mdst.append(i)
      i += 1
    else:
      i += 1
  return msrc, mdst, hp, hq

def apply_mutation(row, mut):
  msrc, mdst, hp, hq = mut
  return infill(len(row) + len(hp), apply_permutation(row, msrc, mdst), hp, hq)

def brute2(n, d, header, seeds):
  sofar = []
  def recur(i):
    # we did it
    if i >= len(header):
      yield deepcopy(sofar)
      return

    for seed in seeds:
      mut = deduce_mutation(n, d, seed[0], header[0])
      block = []
      good = True
      for row in seed:
        u = apply_mutation(row, mut)
        for v in sofar:
          if not separated(u,v,d):
            good = False
        if not good:
          break
        block.append(u)

      if not good:
        continue

      sofar.extend(block)
      yield from recur(i+1)
      for _ in block:
        sofar.pop()

  yield from recur(0)



def kitty_combos(n, d):
  if d == 0:
    yield []
  if n <= 0 or d <= 0:
    return

  # don't put it here
  yield from kitty_combos(n-1, d)

  # put it here
  for row in kitty_combos(n-1, d-1):
    yield row + [n-1]


## Show itertools combinations
# for row in it.combinations(list(range(8)), 3):
#   print(row)


## Show kitty combos
# for row in enumerate(kitty_combos(8, 3)):
for row in kitty_combos(8, 3):
  # print(row)
  pot = [0]*8
  for e in row:
    pot[e] = 1
  print(pot)


## Start with an (8,3) as the heade
# seeds = list(make_seeds(5, 3))
# for x, header in enumerate(make_seeds(8, 3)):
#   if x % 10000 == 0:
#     print(x)
#   for ret in brute2(8, 3, header, seeds):
#     for row in ret:
#       print(row)
#     print('done')
#     break
#   else:
#     continue
#   break


# for x, seed in enumerate(make_seeds(5, 3)):
#   for row in seed:
#     print(row)
#   print('^'*10, x, '^'*10)



# for asdf in brute(8,3):
#   print('asdf', asdf)