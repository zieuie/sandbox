from collections import Counter
import itertools as it
import random


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def disagreement_counter(pa, d):
  ret = []
  c = Counter()
  for vx, v in enumerate(pa):
    for ux in range(vx):
      u = pa[ux]
      separated = False
      for a,b in zip(u,v):
        if abs(a-b) >= d:
          separated = True
          break
      if not separated:
        ret.append((ux,vx))
        c.update([ux])
        c.update([vx])
  # return ret
  return c


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def dumb_pa(n, k):
  m = n-k
  sr = set(range(n))
  A = []
  H = list(it.combinations(list(range(n)), k))
  L = [sorted(sr-set(e)) for e in H]
  for ps in H:
    lows = tuple(range(m))
    highs = tuple(range(m, n))
    h, l = 0, 0
    row = []
    for i in range(n):
      if i in ps:
        row.append(highs[h])
        h += 1
      else:
        row.append(lows[l])
        l += 1
    A.append(row)
  return A, H, L


def init_separations(A, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def kid_permutation(A, start, s, i, d):
  target = [e for e in start]
  random.shuffle(target)
  adders, subers, news = eval_permutation(A, start, target, s, i, d)
  return target, adders, subers, news


def eval_permutation(A, start, target, s, i, d):
  pot = apply_permutation(A[i], start, target)
  news = []
  adders = []
  subers = []
  for x, e in enumerate(A):
    if x == i:
      continue
    if separated(pot, e, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news


def hammer(A, d):
  dd=d*d
  asdf = dict()
  # s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx:
        count = 0
        for a,b in zip(A[ux],A[vx]):
          if (a-b)**2 >= dd:
            count += 1
        if count > 1:
          asdf.setdefault(count, []).append((ux,vx))
  return asdf
  # print(list(reversed(sorted(asdf.keys()))))


# Mutates A, s
def mist(A,H,L,s,d):
  asdf = hammer(A, d)
  print(list(reversed(sorted((k,len(v)) for k,v in asdf.items()))))
  while True:
    for k in reversed(sorted(asdf.keys())):
    # for k in sorted(asdf.keys()):
      random.shuffle(asdf[k]) 
      for v in asdf[k]:
        for i in v:
          for _ in range(1000):
            # i = random.randrange(len(A)) if givens is None else random.choice(givens)
            one = random.choice((H, L))[i]
            two, adders, subers, news = kid_permutation(A, one, s, i, d)
            if len(news) + len(adders) >= 2*len(subers):
              return i, one, two, adders, subers, news

def gentle_fist(A, H, L, s, d):
  i, one, two, adders, subers, news = mist(A,H,L,s,d)
  A[i] = apply_permutation(A[i], one, two)
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)
  return len(news) + len(adders) > 2*len(subers)


# Mutates A, s
def gently_disturb(A, H, L, s, d, givens=None):
  while True:
    i = random.randrange(len(A)) if givens is None else random.choice(givens)
    one = random.choice((H, L))[i]
    two, adders, subers, news = kid_permutation(A, one, s, i, d)
    if len(news) + len(adders) >= 2*len(subers):
      break

  A[i] = apply_permutation(A[i], one, two)
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)
  return len(news) + len(adders) > 2*len(subers)


# Mutates A, s
def random_good_permutation(A, H, L, s, i, d, loops=10):
  for _ in range(loops):
    one = random.choice((H, L))[i]
    two, adders, subers, news = kid_permutation(A, one, s, i, d)
    if len(news) + len(adders) >= 2*len(subers):
      A[i] = apply_permutation(A[i], one, two)
      s[i].update(news)
      for x in adders:
        s[x].add(i)
      for x in subers:
        s[i].discard(x)
        s[x].discard(i)
      return True
  return False    


# Mutates A, s
def best_permutation(A, one, s, i, d):
  best = None
  for two in it.permutations(one):
    adders, subers, news = eval_permutation(A, one, two, s, i, d)
    w = len(news) + len(adders) - 2*len(subers)
    if best is None or w > best[0]:
      best = w, two, adders, subers, news

  w, two, adders, subers, news = best
  if w <= 0:
    return False

  A[i] = apply_permutation(A[i], one, two)
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)
  return True  


# Mutates A, s
def best_full_permutation(A, H, L, s, i, d):
  best = None
  one = list(H[i]) + L[i]
  for ht in it.permutations(H[i]):
   for lt in it.permutations(L[i]):
    two = ht+lt
    adders, subers, news = eval_permutation(A, one, two, s, i, d)
    w = len(news) + len(adders) - 2*len(subers)
    if best is None or w > best[0]:
      best = w, two, adders, subers, news

  w, two, adders, subers, news = best
  if w <= 0:
    return False

  A[i] = apply_permutation(A[i], one, two)
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)
  return True  


def greatly_disturb(A, H, L, s, d):
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  # i = random.randrange(10)
  i = q[-1][1]

  hps = [e for e in H[i]]
  random.shuffle(hps)

  lps = [e for e in L[i]]
  random.shuffle(lps)

  one = list(H[i]) + list(L[i])
  two = hps + lps
  adders, subers, news = eval_permutation(A, one, two, s, i, d)

  A[i] = apply_permutation(A[i], one, two)
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)
